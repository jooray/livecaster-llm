"""LLM block and annotated outline rendering (M1)."""

from __future__ import annotations

from pathlib import Path

from livecaster.outline.parser import parse_outline
from livecaster.outline.render import coverage_fraction, render_annotated, render_for_llm
from livecaster.session.models import NodeState


def test_llm_block_contains_every_node_exactly_once(outline):
    block = render_for_llm(outline)
    lines = block.split("\n")
    assert len(lines) == len(outline.nodes)
    for node in outline.nodes:
        assert sum(1 for line in lines if line.startswith(f"[{node.id}] ")) == 1


def test_llm_block_shows_structure(outline):
    block = render_for_llm(outline)
    assert "[T4] ## Úvod / kontext" in block
    assert "[T5]   - Prečo práve teraz dych?" in block
    assert "[T23]     - Prečo sa venovať dychu" in block
    assert block.splitlines()[8].startswith("[T9]   ¶ ")


def test_llm_block_has_no_status_so_the_prefix_cache_holds(outline):
    a = render_for_llm(outline)
    b = render_for_llm(outline)
    assert a == b
    assert "covered" not in a and "✅" not in a


def test_annotated_round_trip_is_byte_identical(outline, osnova_path: Path):
    original = osnova_path.read_text(encoding="utf-8")
    assert render_annotated(outline, {}, original) == original


def test_annotated_markers(outline, osnova_path: Path):
    original = osnova_path.read_text(encoding="utf-8")
    states = {
        "T5": NodeState(id="T5", status="covered", covered_at=192.0),
        "T15": NodeState(id="T15", status="touched"),
        "T29": NodeState(id="T29", status="skipped"),
    }
    out = render_annotated(outline, states, original)
    assert "- ~~Prečo práve teraz dych? (Juraj + Zuzka mali session)~~ ✅ 00:03:12" in out
    assert "koncentrácia, úzkosť ◐" in out
    assert "kombinujú obe? ⏭" in out
    assert "## Úvod / kontext (1/3)" in out
    # Untouched sections stay untouched.
    assert "## 4. Lunarpunk / paralelná cesta\n" in out


def test_annotated_chapter_times_are_relative_to_the_sync_mark(outline, osnova_path: Path):
    original = osnova_path.read_text(encoding="utf-8")
    states = {"T5": NodeState(id="T5", status="covered", covered_at=192.0)}
    out = render_annotated(outline, states, original, sync_offset=60.0)
    assert "✅ 00:02:12" in out


def test_annotated_appends_new_topics(outline, osnova_path: Path):
    original = osnova_path.read_text(encoding="utf-8")
    out = render_annotated(
        outline, {}, original, new_topics=[{"title": "Neurofeedback", "summary": "mimo osnovy"}]
    )
    assert out.startswith(original)
    assert "Neurofeedback" in out.replace(original, "")


def test_multiline_paragraph_strikethrough():
    text = "## H\n\nfirst line\nsecond line\n"
    outline = parse_outline(text)
    node = outline.leaves()[0]
    out = render_annotated(outline, {node.id: NodeState(id=node.id, status="covered", covered_at=5)}, text)
    assert out.split("\n")[2] == "~~first line"
    assert out.split("\n")[3] == "second line~~ ✅ 00:00:05"


def test_coverage_fraction_counts_skipped_as_done(outline):
    heading = next(n for n in outline.nodes if n.text.startswith("Úvod"))
    states = {
        "T5": NodeState(id="T5", status="covered"),
        "T6": NodeState(id="T6", status="skipped"),
        "T7": NodeState(id="T7", status="touched"),
    }
    assert coverage_fraction(outline, heading.id, states) == (2, 3)
