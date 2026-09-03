"""Export rendering from the canned final analysis (M6)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from livecaster.llm.schemas import FinalAnalysis
from livecaster.replay import load_transcript_fixture
from livecaster.session.exports import (
    render_show_notes,
    render_srt,
    render_transcript_md,
    write_exports,
)
from livecaster.session.models import NodeState, Segment, Session


@pytest.fixture
def analysis(fixtures) -> FinalAnalysis:
    return FinalAnalysis.model_validate(json.loads((fixtures / "final_analysis.json").read_text()))


@pytest.fixture
def session(outline) -> Session:
    s = Session(id="2026-09-03_osnova", outline=outline.nodes, language="sk", duration_s=347.7)
    s.nodes["T5"] = NodeState(id="T5", status="covered", covered_at=18.0)
    s.nodes["T15"] = NodeState(id="T15", status="covered", covered_at=132.0)
    s.nodes["T29"] = NodeState(id="T29", status="skipped")
    return s


@pytest.fixture
def segments(fixtures) -> list[Segment]:
    return load_transcript_fixture(fixtures / "transcript_sk.jsonl")


def test_show_notes_uses_the_models_labels(analysis, session, outline):
    md = render_show_notes(analysis, session, outline)
    assert "## Zhrnutie" in md
    assert "## Kapitoly" in md
    assert "## Nestihli sme" in md
    assert "## Spomenuté / odkazy" in md
    assert "Summary" not in md


def test_show_notes_falls_back_to_english_labels(analysis, session, outline):
    analysis.labels.pop("chapters")
    md = render_show_notes(analysis, session, outline)
    assert "## Chapters" in md
    assert "## Zhrnutie" in md


def test_show_notes_has_every_required_section(analysis, session, outline):
    md = render_show_notes(analysis, session, outline)
    for label in analysis.labels.values():
        assert f"## {label}" in md


def test_mentions_get_a_url_or_a_todo(analysis, session, outline):
    md = render_show_notes(analysis, session, outline)
    assert "🔗 https://dychova-praca.example" in md
    assert "TODO link: `Wim Hof method`" in md
    for mention in analysis.mentions:
        assert mention.text in md


def test_chapter_times_are_relative_to_the_sync_mark(analysis, session, outline):
    plain = render_show_notes(analysis, session, outline)
    assert "`00:00:05` **Úvod a predstavenie**" in plain

    session.sync_marks = [65.0]
    shifted = render_show_notes(analysis, session, outline)
    assert "`00:00:00` **Úvod a predstavenie**" in shifted  # clamped at zero
    assert "`00:00:30` **Fyziológia výdychu**" in shifted  # 95 - 65


def test_chapter_node_labels_are_truncated(analysis, session, outline):
    md = render_show_notes(analysis, session, outline)
    chapters_section = md.split("## Kapitoly", 1)[1].split("\n## ", 1)[0]
    lines = [line for line in chapters_section.split("\n") if line.startswith("- `")]
    assert len(lines) == len(analysis.chapters)
    for line in lines:
        if "** — " not in line:
            continue
        for label in line.split("** — ", 1)[1].split("; "):
            assert len(label) <= 72, label


def test_uncovered_topics_are_listed(analysis, session, outline):
    md = render_show_notes(analysis, session, outline)
    assert "Dych ako denný systémový proces sme nestihli." in md


# --- transcript exports ---------------------------------------------------


def test_transcript_md_groups_by_speaker(segments):
    md = render_transcript_md(segments[:6], show_speakers=True)
    assert md.startswith("# Transcript")
    assert "**[00:00:05] Host**" in md
    assert "**[00:00:11] Guest**" in md


def test_transcript_md_without_speakers(segments):
    md = render_transcript_md(segments[:6], show_speakers=False)
    assert "Host" not in md
    assert "**[00:00:05]**" in md


SRT_BLOCK = re.compile(
    r"^(\d+)\n(\d{2}:\d{2}:\d{2},\d{3}) --> (\d{2}:\d{2}:\d{2},\d{3})\n(.+)$", re.MULTILINE | re.DOTALL
)


def _parse_srt(text: str) -> list[tuple[int, str, str, str]]:
    blocks = [b for b in text.strip().split("\n\n") if b.strip()]
    out = []
    for block in blocks:
        lines = block.split("\n")
        start, _, end = lines[1].partition(" --> ")
        out.append((int(lines[0]), start, end, "\n".join(lines[2:])))
    return out


def _to_seconds(stamp: str) -> float:
    hms, _, ms = stamp.partition(",")
    h, m, s = (int(x) for x in hms.split(":"))
    return h * 3600 + m * 60 + s + int(ms) / 1000


def test_srt_is_valid_monotonic_and_short(segments):
    srt = render_srt(segments, show_speakers=True)
    cues = _parse_srt(srt)
    assert len(cues) >= len(segments)
    prev_end = 0.0
    for _index, start, end, text in cues:
        s, e = _to_seconds(start), _to_seconds(end)
        assert e > s
        assert e - s <= 7.5
        assert s >= prev_end - 1e-6
        prev_end = e
        assert text.strip()
    assert [c[0] for c in cues] == list(range(1, len(cues) + 1))


def test_srt_offsets_by_the_sync_mark(segments):
    srt = render_srt(segments[:2], show_speakers=True, sync_offset=5.0)
    assert srt.startswith("1\n00:00:00,000 -->")


def test_srt_splits_long_segments_on_word_timestamps():
    from livecaster.session.models import Word

    words = [Word(start=i * 0.5, end=i * 0.5 + 0.5, text=f"w{i}") for i in range(40)]
    segment = Segment(
        id="S1", channel="Host", t0=0.0, t1=20.0, text=" ".join(w.text for w in words), words=words
    )
    cues = _parse_srt(render_srt([segment], show_speakers=False))
    assert len(cues) >= 3
    assert all(_to_seconds(c[2]) - _to_seconds(c[1]) <= 7.5 for c in cues)
    joined = " ".join(c[3] for c in cues).split()
    assert joined == [w.text for w in words]


def test_srt_splits_long_segments_without_word_timestamps():
    segment = Segment(id="S1", channel="Host", t0=0.0, t1=21.0, text=" ".join(f"w{i}" for i in range(30)))
    cues = _parse_srt(render_srt([segment], show_speakers=False))
    assert len(cues) >= 3
    assert " ".join(c[3] for c in cues).split() == [f"w{i}" for i in range(30)]


# --- write_exports --------------------------------------------------------


def test_write_exports_writes_all_five_files(
    tmp_path: Path, analysis, session, outline, segments, osnova_path
):
    paths = write_exports(
        tmp_path,
        analysis,
        session,
        outline,
        segments,
        osnova_path.read_text(encoding="utf-8"),
        show_speakers=True,
    )
    assert set(paths) == {
        "show_notes",
        "outline_annotated",
        "transcript_md",
        "transcript_srt",
        "final_analysis",
    }
    for path in paths.values():
        assert Path(path).is_file() and Path(path).stat().st_size > 0
    annotated = Path(paths["outline_annotated"]).read_text(encoding="utf-8")
    assert "✅ 00:00:18" in annotated
    assert "⏭" in annotated
    assert "Neurofeedback" not in annotated  # new topics come from the session, not the analysis
    round_tripped = json.loads(Path(paths["final_analysis"]).read_text(encoding="utf-8"))
    assert round_tripped["language"] == "sk"


def test_srt_trims_the_earlier_cue_when_two_channels_overlap():
    """People talk over each other; timings must stay anchored to the recording."""
    segments = [
        Segment(id="S1", channel="Host", speaker="Host", t0=10.0, t1=16.0, text="a b c"),
        Segment(id="S2", channel="Guest", speaker="Guest", t0=14.0, t1=18.0, text="d e f"),
        Segment(id="S3", channel="Host", speaker="Host", t0=19.0, t1=22.0, text="g h i"),
    ]
    cues = _parse_srt(render_srt(segments, show_speakers=True))
    starts = [_to_seconds(c[1]) for c in cues]
    ends = [_to_seconds(c[2]) for c in cues]
    assert starts == [10.0, 14.0, 19.0]  # never pushed forward
    assert ends[0] == 14.0  # the first cue is trimmed, not the second delayed
    assert all(ends[i] <= starts[i + 1] + 1e-6 for i in range(len(cues) - 1))


def test_srt_keeps_a_minimum_cue_length_on_a_pile_up():
    segments = [
        Segment(id=f"S{i}", channel="Host", speaker="Host", t0=5.0, t1=5.05, text=f"w{i}") for i in range(4)
    ]
    cues = _parse_srt(render_srt(segments, show_speakers=False))
    for _index, start, end, _text in cues:
        assert _to_seconds(end) - _to_seconds(start) >= 0.19


def test_srt_drops_empty_cues():
    segments = [Segment(id="S1", channel="Host", t0=0.0, t1=3.0, text="   ")]
    assert render_srt(segments, show_speakers=False).strip() == ""
