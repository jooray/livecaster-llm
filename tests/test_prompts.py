"""Prompt builders and the transcript window (M2, PLAN §5.3)."""

from __future__ import annotations

from livecaster.llm.prompts import (
    build_final_messages,
    build_preflight_messages,
    build_tick_system,
    build_tick_user,
    language_name,
    render_transcript,
    trim_to_word_budget,
)
from livecaster.session.models import Mention, NodeState, Segment, Session


def _session(outline) -> Session:
    return Session(id="s", outline=outline.nodes, language="sk")


def _segments(n: int = 6, words: int = 10, speakers=("Host", "Guest")) -> list[Segment]:
    out = []
    for i in range(n):
        out.append(
            Segment(
                id=f"S{i + 1}",
                channel=speakers[i % len(speakers)],
                speaker=speakers[i % len(speakers)],
                t0=float(i * 10),
                t1=float(i * 10 + 8),
                text=" ".join(f"w{i}x{j}" for j in range(words)),
            )
        )
    return out


def test_system_message_is_identical_across_ticks(outline):
    a = build_tick_system(outline, "sk")
    b = build_tick_system(outline, "sk")
    assert a == b
    assert "[T29]" in a
    assert "Slovak (sk)" in a


def test_system_message_ignores_state(outline):
    session = _session(outline)
    a = build_tick_system(outline, session.language)
    session.nodes["T5"] = NodeState(id="T5", status="covered", covered_at=10.0)
    b = build_tick_system(outline, session.language)
    assert a == b


def test_user_message_carries_state_mentions_and_the_new_marker(outline):
    session = _session(outline)
    session.nodes["T5"] = NodeState(id="T5", status="covered", covered_at=192.0)
    session.nodes["T6"] = NodeState(id="T6", status="touched")
    session.nodes["T30"] = NodeState(id="T30", status="skipped")
    session.mentions.append(Mention(id="M1", kind="person", text="Wim Hof"))
    segments = _segments()
    user = build_tick_user(session, outline, segments, "S4", 123.0, show_speakers=True)
    assert "SESSION TIME: 00:02:03" in user
    assert "T5(00:03:12)" in user
    assert "touched: T6" in user
    assert "skipped: T30" in user
    assert "Wim Hof (person)" in user
    assert user.index("--- NEW SINCE LAST TICK ---") < user.index("[00:00:40 Host]")
    assert "[00:00:30 Guest]" in user.split("--- NEW SINCE LAST TICK ---")[0]


def test_single_microphone_mode_omits_speakers(outline):
    session = _session(outline)
    segments = [Segment(id="S1", channel="Room", speaker=None, t0=0, t1=3, text="ahoj")]
    user = build_tick_user(session, outline, segments, None, 5.0, show_speakers=False)
    assert "[00:00:00] ahoj" in user
    assert "Host" not in user.split("TRANSCRIPT")[1]


def test_window_trimming_keeps_the_new_part(outline):
    segments = _segments(n=20, words=20)
    keep = {s.id for s in segments[-3:]}
    trimmed = trim_to_word_budget(segments, 50, keep)
    assert keep.issubset({s.id for s in trimmed})
    assert sum(s.word_count() for s in trimmed) <= 80


def test_render_transcript_marks_the_boundary_even_when_nothing_is_new():
    segments = _segments(n=2)
    out = render_transcript(segments, "S2", show_speakers=True)
    assert out.strip().endswith("--- NEW SINCE LAST TICK ---")


def test_preflight_messages(outline):
    messages = build_preflight_messages(outline, "sk")
    assert messages[0]["role"] == "system" and "Slovak" in messages[0]["content"]
    assert "COVERABLE NODE IDS: T5, T6" in messages[1]["content"]


def test_final_messages_include_full_transcript_and_states(outline):
    session = _session(outline)
    session.duration_s = 3600.0
    session.sync_marks = [42.0]
    session.nodes["T5"] = NodeState(id="T5", status="covered", covered_at=192.0)
    messages = build_final_messages(session, outline, _segments(), show_speakers=True, language="sk")
    body = messages[1]["content"]
    assert "SESSION DURATION: 01:00:00" in body
    assert "SYNC OFFSET (seconds of session time where the external recording starts): 42.0" in body
    assert "T5: covered @00:03:12" in body
    assert "FULL TRANSCRIPT:" in body
    assert "Slovak" in messages[0]["content"]


def test_language_name_table():
    assert language_name("sk") == "Slovak"
    assert language_name("cs") == "Czech"
    assert language_name("en") == "English"
    assert language_name(None).startswith("the language")
    assert language_name("zz") == "zz"
