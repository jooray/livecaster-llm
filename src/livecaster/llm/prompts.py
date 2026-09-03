"""Prompt builders (SPEC Appendix A)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from livecaster.outline.model import Outline
from livecaster.outline.render import render_for_llm
from livecaster.session.models import Mention, NewTopic, NodeState, Segment, Session
from livecaster.timeutil import fmt_hms

CHARS_PER_TOKEN = 3.2

LANGUAGE_NAMES = {
    "sk": "Slovak",
    "cs": "Czech",
    "en": "English",
    "de": "German",
    "pl": "Polish",
    "hu": "Hungarian",
    "es": "Spanish",
    "fr": "French",
    "it": "Italian",
    "nl": "Dutch",
    "uk": "Ukrainian",
    "ru": "Russian",
    "pt": "Portuguese",
}


def language_name(code: str | None) -> str:
    if not code:
        return "the language of the conversation"
    return LANGUAGE_NAMES.get(code.lower(), code)


def estimate_tokens(text: str) -> int:
    return int(len(text) / CHARS_PER_TOKEN)


TICK_SYSTEM_TEMPLATE = """\
You are the live co-pilot of a podcast host. You receive the host's outline (a numbered map of \
topics, NOT a script), the current state of which topics were already discussed, and the most \
recent transcript. Your job is to keep the map accurate and to notice opportunities.

Rules:
1. The outline order does not matter. The best next topic is the one with the most natural bridge \
from what was just said, wherever it sits in the outline.
2. Be conservative with "covered": only when the substance of the item was actually discussed. \
A passing mention is "touched".
3. Every "covered" entry must quote a short verbatim fragment of the transcript as evidence and \
give its timestamp.
3b. Report only what CHANGED. Never repeat an item that STATE already lists as covered, touched \
or skipped, and never repeat a mention that MENTIONS SO FAR already lists. Most ticks should \
return one or two entries per array, often none.
4. "hot" = topics that became relevant right now because of something said in the NEW part of the \
transcript. Skip topics already covered. For each one give:
   - "label": how the host should recognise it at a glance. AT MOST 5 WORDS, no punctuation, \
     not a sentence. Name the thing, do not describe it. Good: "Wim Hof vs. her work". \
     Bad: "The difference between her work and the Wim Hof method, in terms of intensity".
   - "reason": at most 12 words, why now.
   - "segue": one short sentence the host could say out loud.
5. Suggest at most 5 follow-up questions for the current moment. Short, concrete, in the voice of \
a curious host. One sentence each, no preamble.
6. Extract mentions worth linking in the show notes: people, books, articles, tools, products, \
places, events, and promises like "we will put the link in the description". Set "url" only if you \
are certain it is a real URL; otherwise null and a search query.
7. If the conversation goes somewhere not in the outline for more than a few sentences, report it \
in "new_topics".
8. Focus on the NEW part of the transcript; use the earlier part only as context.
8b. Speaker labels may be absent (single shared microphone). Then infer who is speaking only when \
the content makes it obvious, and never state a speaker as fact.
9. All human-facing text (summary, reasons, segues, questions, notes) must be in the conversation \
language: {language_name} ({language_code}). Never switch to English unless the conversation is in \
English.
10. The host reads this mid-sentence, live, while talking. Every string is a glance, not a \
paragraph: "current.summary" at most 12 words, and no field ever repeats what the outline text \
already says.
11. Output only JSON matching the schema.

OUTLINE (id, structure, text):
{outline_block}"""


def build_tick_system(outline: Outline, language: str | None) -> str:
    """System message. Byte-identical for a whole session so the prefix cache hits."""
    return TICK_SYSTEM_TEMPLATE.format(
        language_name=language_name(language),
        language_code=language or "auto",
        outline_block=render_for_llm(outline),
    )


def _state_line(session: Session, outline: Outline, hot_last: Sequence[str]) -> str:
    covered, touched, skipped, pinned = [], [], [], []
    for node_id, st in session.nodes.items():
        if st.status == "covered":
            covered.append(f"{node_id}({fmt_hms(st.covered_at)})")
        elif st.status == "touched":
            touched.append(node_id)
        elif st.status == "skipped":
            skipped.append(node_id)
        if st.pinned:
            pinned.append(node_id)
    parts = []
    parts.append("covered: " + (", ".join(sorted(covered)) if covered else "—"))
    parts.append("touched: " + (", ".join(sorted(touched)) if touched else "—"))
    parts.append("skipped: " + (", ".join(sorted(skipped)) if skipped else "—"))
    parts.append("pinned: " + (", ".join(sorted(pinned)) if pinned else "—"))
    parts.append("hot last tick: " + (", ".join(hot_last) if hot_last else "—"))
    return " · ".join(parts)


def _mentions_line(mentions: Iterable[Mention], limit: int = 40) -> str:
    items = list(mentions)[-limit:]
    if not items:
        return "—"
    return ", ".join(f"{m.text} ({m.kind})" for m in items)


def _new_topics_line(topics: Iterable[NewTopic]) -> str:
    items = list(topics)
    if not items:
        return "—"
    return "; ".join(t.title for t in items)


def render_transcript(
    segments: Sequence[Segment],
    new_since_id: str | None,
    *,
    show_speakers: bool,
) -> str:
    """Transcript lines with a marker before the first segment of the new part."""
    lines: list[str] = []
    marker_done = new_since_id is None
    new_ids: set[str] = set()
    if new_since_id is not None:
        seen = False
        for s in segments:
            if seen:
                new_ids.add(s.id)
            if s.id == new_since_id:
                seen = True
        if not seen:
            new_ids = {s.id for s in segments}
    for s in segments:
        if not marker_done and s.id in new_ids:
            lines.append("--- NEW SINCE LAST TICK ---")
            marker_done = True
        stamp = fmt_hms(s.t0)
        if show_speakers and s.speaker:
            lines.append(f"[{stamp} {s.speaker}] {s.text}")
        else:
            lines.append(f"[{stamp}] {s.text}")
    if not marker_done:
        lines.append("--- NEW SINCE LAST TICK ---")
    return "\n".join(lines)


def trim_to_word_budget(segments: Sequence[Segment], words: int, keep_ids: set[str]) -> list[Segment]:
    """Drop whole segments from the front until the budget fits; never drop ``keep_ids``."""
    out = list(segments)
    total = sum(s.word_count() for s in out)
    i = 0
    while total > words and i < len(out):
        if out[i].id in keep_ids:
            i += 1
            continue
        total -= out[i].word_count()
        out.pop(i)
    return out


def build_tick_user(
    session: Session,
    outline: Outline,
    segments: Sequence[Segment],
    new_since_id: str | None,
    now_t: float,
    *,
    show_speakers: bool,
    hot_last: Sequence[str] = (),
    window_words: int = 1200,
) -> str:
    keep = {s.id for s in segments} if new_since_id is None else set()
    if new_since_id is not None:
        seen = False
        for s in segments:
            if seen:
                keep.add(s.id)
            if s.id == new_since_id:
                seen = True
    trimmed = trim_to_word_budget(segments, window_words, keep)
    return (
        f"SESSION TIME: {fmt_hms(now_t)}\n"
        f"STATE: {_state_line(session, outline, hot_last)}\n"
        f"MENTIONS SO FAR: {_mentions_line(session.mentions)}\n"
        f"NEW TOPICS SO FAR: {_new_topics_line(session.suggestions.new_topics)}\n"
        "\n"
        "TRANSCRIPT (older context first, then the NEW part):\n"
        f"{render_transcript(trimmed, new_since_id, show_speakers=show_speakers)}\n"
        "\n"
        "TASK: Update the map for the NEW part. Return the JSON."
    )


PREFLIGHT_SYSTEM = """\
Read the podcast outline. For each coverable node return 2-3 sharp questions the host could ask, \
3-6 trigger phrases (words a guest might say that signal this topic; include the conversation \
language and English variants), and IDs of related nodes elsewhere in the outline. Write in \
{language_name}. Only use node IDs that appear in the outline. Output only JSON."""


def build_preflight_messages(outline: Outline, language: str | None) -> list[dict[str, str]]:
    coverable = ", ".join(n.id for n in outline.leaves())
    return [
        {"role": "system", "content": PREFLIGHT_SYSTEM.format(language_name=language_name(language))},
        {
            "role": "user",
            "content": (
                "OUTLINE:\n"
                f"{render_for_llm(outline)}\n\n"
                f"COVERABLE NODE IDS: {coverable}\n\n"
                "TASK: Return questions, triggers and related IDs for every coverable node."
            ),
        },
    ]


FINAL_SYSTEM = """\
You are producing show notes for a finished podcast episode. You receive the outline, the full \
transcript with timestamps and speakers, the live state (covered/touched/skipped), collected \
mentions and new topics. Write everything in {language_name} ({language_code}).

Requirements:
- "summary": 3-6 sentences, each a separate array item, describing what the episode is actually about.
- "chapters": follow the actual conversation, 5-15 chapters, start/end times taken from the \
transcript timestamps (session seconds, not formatted), titles short and specific, node_ids of the \
outline items the chapter covers when there are any.
- "covered" / "uncovered": node IDs from the outline with a one-line note. "uncovered" is what the \
host did not get to and might use for a next episode.
- "titles": 8-12 candidates in varied styles (descriptive, curiosity, quote, question, short).
- "description_short" (<= 300 characters) and "description_long" (<= 1500 characters) must be \
publishable as they are.
- "social": two variants for different platforms.
- "quotes": verbatim fragments from the transcript with their timestamp; never paraphrase.
- "mentions": everything worth linking. Include a URL only if you are certain it is real; \
otherwise null plus a search query in "search_query".
- "promises": moments where somebody promised something for the show notes.
- "labels": the section headings of the show notes translated to {language_name}.

Speaker labels may be absent (single shared microphone); then leave "speaker" null in quotes and \
never state who spoke as fact. Output only JSON."""


def build_final_messages(
    session: Session,
    outline: Outline,
    segments: Sequence[Segment],
    *,
    show_speakers: bool,
    language: str | None,
) -> list[dict[str, str]]:
    transcript = render_transcript(segments, None, show_speakers=show_speakers)
    states = _final_state_block(session, outline)
    sync = session.sync_offset
    user = (
        f"PODCAST LANGUAGE: {language or 'auto'}\n"
        f"SESSION DURATION: {fmt_hms(session.duration_s)}\n"
        f"SYNC OFFSET (seconds of session time where the external recording starts): {sync:.1f}\n"
        "\n"
        f"OUTLINE:\n{render_for_llm(outline)}\n"
        "\n"
        f"LIVE STATE:\n{states}\n"
        "\n"
        f"MENTIONS COLLECTED LIVE: {_mentions_line(session.mentions, limit=200)}\n"
        f"NEW TOPICS COLLECTED LIVE: {_new_topics_line(session.suggestions.new_topics)}\n"
        "\n"
        f"FULL TRANSCRIPT:\n{transcript}\n"
        "\n"
        "TASK: Produce the show notes JSON."
    )
    return [
        {
            "role": "system",
            "content": FINAL_SYSTEM.format(
                language_name=language_name(language), language_code=language or "auto"
            ),
        },
        {"role": "user", "content": user},
    ]


def _final_state_block(session: Session, outline: Outline) -> str:
    rows: list[str] = []
    for node in outline.nodes:
        if not node.coverable:
            continue
        st: NodeState | None = session.nodes.get(node.id)
        status = st.status if st else "untouched"
        stamp = f" @{fmt_hms(st.covered_at)}" if st and st.covered_at is not None else ""
        rows.append(f"{node.id}: {status}{stamp}")
    return " · ".join(rows) if rows else "—"


LINKS_SYSTEM = """\
You resolve links for podcast show notes. For each item you are given, return the canonical URL \
only when a web search result you actually retrieved supports it. If you are not certain, return \
null. Never invent a URL. Output only JSON."""


def build_links_messages(mentions: Sequence[Mapping[str, str] | Mention]) -> list[dict[str, str]]:
    rows = []
    for m in mentions:
        text = m["text"] if isinstance(m, Mapping) else m.text
        kind = m["kind"] if isinstance(m, Mapping) else m.kind
        query = (m.get("search_query") if isinstance(m, Mapping) else m.search_query) or text
        rows.append(f"- {text} ({kind}) — search: {query}")
    return [
        {"role": "system", "content": LINKS_SYSTEM},
        {"role": "user", "content": "ITEMS:\n" + "\n".join(rows) + "\n\nTASK: Return the JSON."},
    ]
