"""Render the session's final artefacts (FR-29, FR-30)."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from livecaster.llm.schemas import ENGLISH_LABELS, FinalAnalysis
from livecaster.outline.model import Outline
from livecaster.outline.render import render_annotated
from livecaster.session.models import Segment, Session
from livecaster.timeutil import fmt_hms, fmt_srt_time

TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "templates"

KIND_ICONS = {
    "person": "👤",
    "book": "📖",
    "article": "📰",
    "link": "🔗",
    "tool": "🛠",
    "product": "📦",
    "place": "📍",
    "event": "🎪",
    "concept": "💡",
    "promise": "🤝",
    "other": "•",
}

MAX_SRT_SECONDS = 7.0


def make_env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )
    env.filters["hms"] = fmt_hms
    return env


def _label(analysis: FinalAnalysis, key: str) -> str:
    return analysis.labels.get(key) or ENGLISH_LABELS[key]


def group_mentions(analysis: FinalAnalysis) -> list[dict[str, object]]:
    groups: dict[str, list[dict[str, object]]] = {}
    for m in analysis.mentions:
        kind = m.kind if m.kind in KIND_ICONS else "other"
        groups.setdefault(kind, []).append(
            {
                "text": m.text,
                "context": m.context,
                "url": m.url,
                "needs_link": bool(m.needs_link and not m.url),
                "search_query": m.search_query or m.text,
            }
        )
    order = [k for k in KIND_ICONS if k in groups]
    return [{"kind": k, "icon": KIND_ICONS[k], "items": groups[k]} for k in order]


def node_label(outline: Outline, node_id: str, max_len: int = 70) -> str:
    """Short label for a node, for chapter and coverage lists."""
    node = outline.get(node_id)
    text = node.text if node else node_id
    if len(text) <= max_len:
        return text
    cut = text[:max_len].rsplit(" ", 1)[0]
    return cut.rstrip(" ,;:—-") + "…"


def missed_must_asks(session: Session, outline: Outline) -> list[dict[str, object]]:
    """Must-asks that never got covered (FR-40).

    Computed from the reducer's own state rather than from the model's `uncovered`
    list, for the same reason the reducer exists at all: the model proposes, and
    ordinary code decides what is true. Skipped counts as answered — the host said
    so on purpose.
    """
    out: list[dict[str, object]] = []
    for node in outline.nodes:
        if not node.must:
            continue
        state = session.nodes.get(node.id)
        status = state.status if state else "untouched"
        if status in ("covered", "skipped"):
            continue
        out.append({"text": node_label(outline, node.id), "touched": status == "touched"})
    return out


def render_show_notes(
    analysis: FinalAnalysis,
    session: Session,
    outline: Outline,
    *,
    sync_offset: float | None = None,
) -> str:
    offset = session.sync_offset if sync_offset is None else sync_offset
    env = make_env()
    template = env.get_template("show_notes.md.j2")
    chapters = [
        {
            "title": c.title,
            "start": fmt_hms(max(0.0, c.start_t - offset)),
            "end": fmt_hms(max(0.0, c.end_t - offset)),
            "nodes": [node_label(outline, n) for n in c.node_ids],
        }
        for c in analysis.chapters
    ]
    return template.render(
        title=analysis.titles[0].text if analysis.titles else session.id,
        label=lambda k: _label(analysis, k),
        analysis=analysis,
        chapters=chapters,
        covered=[{"text": node_label(outline, c.node_id), "note": c.note} for c in analysis.covered],
        uncovered=[{"text": node_label(outline, c.node_id), "note": c.note} for c in analysis.uncovered],
        missed_must=missed_must_asks(session, outline),
        quotes=[
            {"t": fmt_hms(max(0.0, q.t - offset)), "speaker": q.speaker, "text": q.text}
            for q in analysis.quotes
        ],
        promises=[{"t": fmt_hms(max(0.0, p.t - offset)), "text": p.text} for p in analysis.promises],
        mention_groups=group_mentions(analysis),
        session=session,
        duration=fmt_hms(session.duration_s),
        sync_offset=offset,
    )


def render_transcript_md(
    segments: Sequence[Segment], *, show_speakers: bool, sync_offset: float = 0.0
) -> str:
    env = make_env()
    template = env.get_template("transcript.md.j2")
    blocks: list[dict[str, object]] = []
    last_speaker: str | None = None
    last_stamp = -1e9
    for seg in segments:
        t = max(0.0, seg.t0 - sync_offset)
        new_block = not blocks or (show_speakers and seg.speaker != last_speaker) or (t - last_stamp) >= 60.0
        if new_block:
            blocks.append(
                {
                    "t": fmt_hms(t),
                    "speaker": seg.speaker if show_speakers else None,
                    "text": seg.text,
                }
            )
            last_speaker = seg.speaker
            last_stamp = t
        else:
            blocks[-1]["text"] = f"{blocks[-1]['text']} {seg.text}"
    return template.render(blocks=blocks, show_speakers=show_speakers)


def _split_segment_for_srt(seg: Segment) -> list[tuple[float, float, str]]:
    """Cut a segment into ≤ 7 s cues, on word timestamps when the engine gave them."""
    duration = seg.t1 - seg.t0
    if duration <= MAX_SRT_SECONDS or not seg.text.strip():
        return [(seg.t0, max(seg.t1, seg.t0 + 0.2), seg.text)]
    if seg.words:
        out: list[tuple[float, float, str]] = []
        chunk: list[str] = []
        start = seg.t0 + seg.words[0].start
        prev_end = start
        for w in seg.words:
            w_start = seg.t0 + w.start
            w_end = seg.t0 + w.end
            if chunk and (w_end - start) > MAX_SRT_SECONDS:
                out.append((start, prev_end, " ".join(chunk)))
                chunk = []
                start = w_start
            chunk.append(w.text.strip())
            prev_end = w_end
        if chunk:
            out.append((start, max(prev_end, start + 0.2), " ".join(chunk)))
        return out
    # No word timings: split the text evenly across equal time slices.
    words = seg.text.split()
    parts = max(1, int(duration // MAX_SRT_SECONDS) + 1)
    per = max(1, len(words) // parts + (1 if len(words) % parts else 0))
    out = []
    slice_dur = duration / parts
    for i in range(parts):
        chunk_words = words[i * per : (i + 1) * per]
        if not chunk_words:
            continue
        start = seg.t0 + i * slice_dur
        end = min(seg.t1, start + slice_dur)
        out.append((start, max(end, start + 0.2), " ".join(chunk_words)))
    return out or [(seg.t0, seg.t1, seg.text)]


MIN_CUE_S = 0.2


def render_srt(segments: Sequence[Segment], *, show_speakers: bool, sync_offset: float = 0.0) -> str:
    """SRT cues, ≤ 7 s each, non-overlapping and in order.

    Two channels genuinely overlap when people talk over each other. Rather than
    pushing every later cue forward — which would drift further from the audio with
    every interruption — an overlap trims the *previous* cue back to where the next
    one starts, so timings stay anchored to the recording.
    """
    raw: list[tuple[float, float, str]] = []
    for seg in segments:
        for start, end, text in _split_segment_for_srt(seg):
            body = f"{seg.speaker}: {text}" if (show_speakers and seg.speaker) else text
            body = body.strip()
            if body:
                raw.append((max(0.0, start - sync_offset), max(0.0, end - sync_offset), body))
    raw.sort(key=lambda c: (c[0], c[1]))

    timed: list[list[float | str]] = []
    for start, end, body in raw:
        if timed:
            previous = timed[-1]
            prev_start, prev_end = float(previous[0]), float(previous[1])
            if start < prev_end:
                previous[1] = max(start, prev_start + MIN_CUE_S)
            start = max(start, float(previous[1]))
        timed.append([start, max(end, start + MIN_CUE_S), body])

    env = make_env()
    cues = [
        {
            "index": i,
            "start": fmt_srt_time(float(start)),
            "end": fmt_srt_time(float(end)),
            "text": body,
        }
        for i, (start, end, body) in enumerate(timed, start=1)
    ]
    return env.get_template("transcript.srt.j2").render(cues=cues)


def render_outline_annotated(session: Session, outline: Outline, original_text: str) -> str:
    return render_annotated(
        outline,
        session.nodes,
        original_text,
        retired=session.retired_nodes,
        new_topics=[t.model_dump() for t in session.suggestions.new_topics],
        sync_offset=session.sync_offset,
    )


def write_exports(
    directory: Path,
    analysis: FinalAnalysis,
    session: Session,
    outline: Outline,
    segments: Sequence[Segment],
    original_outline_text: str,
    *,
    show_speakers: bool,
) -> dict[str, str]:
    final = Path(directory)
    final.mkdir(parents=True, exist_ok=True)
    offset = session.sync_offset
    paths = {
        "show_notes": final / "show_notes.md",
        "outline_annotated": final / "outline_annotated.md",
        "transcript_md": final / "transcript.md",
        "transcript_srt": final / "transcript.srt",
        "final_analysis": final / "final_analysis.json",
    }
    paths["show_notes"].write_text(render_show_notes(analysis, session, outline), encoding="utf-8")
    paths["outline_annotated"].write_text(
        render_outline_annotated(session, outline, original_outline_text), encoding="utf-8"
    )
    paths["transcript_md"].write_text(
        render_transcript_md(segments, show_speakers=show_speakers, sync_offset=offset), encoding="utf-8"
    )
    paths["transcript_srt"].write_text(
        render_srt(segments, show_speakers=show_speakers, sync_offset=offset), encoding="utf-8"
    )
    paths["final_analysis"].write_text(analysis.model_dump_json(indent=2), encoding="utf-8")
    return {k: str(v) for k, v in paths.items()}
