"""Outline parsing against the real osnova.md (SPEC §7.1, M1)."""

from __future__ import annotations

import random
from collections import Counter
from pathlib import Path

import pytest

from livecaster.outline.model import normalize
from livecaster.outline.parser import clean_text, is_question_line, parse_outline, strip_inline_md


def test_node_counts(outline):
    kinds = Counter(n.kind for n in outline.nodes)
    assert kinds["heading"] == 9  # one level-1 plus eight level-2
    assert kinds["meta"] == 2
    assert kinds["question"] == 2
    assert kinds["paragraph"] == 7  # six in section 6, one under "## 0."
    assert kinds["item"] == 32
    assert len(outline.leaves()) == 41


def test_ids_are_document_order(outline):
    assert [n.id for n in outline.nodes[:4]] == ["T1", "T2", "T3", "T4"]
    assert outline.nodes[0].text.startswith("Osnova podcastu")


def test_headings_are_not_coverable_when_they_have_leaves(outline):
    for node in outline.nodes:
        if node.kind == "heading":
            assert not node.coverable, f"{node.id} should not be coverable"


def test_meta_lines_before_first_h2(outline):
    metas = [n for n in outline.nodes if n.kind == "meta"]
    assert [m.text_md for m in metas] == [
        "**Dátum:** 2026-08-31",
        "**Cieľ:** Spojiť témy dychu, nervového systému, slobody a autonómie nad vlastným telom. "
        "Prepojiť s predchádzajúcimi epizódami (Peter Kováč, Elena Nováková).",
    ]
    assert not any(m.coverable for m in metas)


def test_nested_item_under_diagnostika(outline):
    parent = next(n for n in outline.nodes if n.text.startswith("Diagnostika"))
    child = next(n for n in outline.nodes if n.text.startswith("Prečo sa venovať dychu"))
    assert child.parent == parent.id
    assert child.id in parent.children
    assert child.level == parent.level + 1 == 1


def test_six_nested_items(outline):
    nested = [n for n in outline.nodes if n.kind in ("item", "question") and n.level >= 1]
    assert len(nested) == 6


def test_question_lines_in_section_four(outline):
    questions = [n for n in outline.nodes if n.kind == "question"]
    assert len(questions) == 2
    assert questions[0].text_md.startswith("**Otázka:**")
    assert questions[1].text_md.startswith("Otázka:")
    assert all(q.coverable for q in questions)
    section = outline.section_of(questions[0].id)
    assert section is not None and section.text.startswith("4. Lunarpunk")


def test_section_six_paragraphs_are_separate_coverable_nodes(outline):
    heading = next(n for n in outline.nodes if n.text.startswith("6. Poznámky z prvej session"))
    paragraphs = [n for n in outline.descendants_of(heading.id) if n.kind == "paragraph"]
    assert len(paragraphs) == 6
    assert all(p.coverable for p in paragraphs)
    assert paragraphs[0].text.startswith("Počas dychového cvičenia")


def test_ansi_escape_is_stripped_but_text_survives(outline):
    node = next(n for n in outline.nodes if n.text.startswith("Počas dychového"))
    # The source carries a real ESC sequence (ESC [118;1:3u), which is removed.
    assert "\x1b" not in node.text
    assert "118;1:3u" not in node.text
    assert "Mal som pocit ako keby som" in node.text


def test_links_are_extracted():
    outline = parse_outline(
        "- See [the paper](https://example.com/paper) and https://dychova-praca.example for more\n"
        "- <https://example.org/x> too\n"
    )
    assert outline.nodes[0].links == ["https://example.com/paper", "https://dychova-praca.example"]
    assert outline.nodes[1].links == ["https://example.org/x"]
    assert outline.nodes[0].text.startswith("See the paper and")


def test_tabs_and_crlf_and_numbered_lists():
    outline = parse_outline("# H\r\n\r\n1. one\r\n2. two\r\n\t- nested\r\n")
    assert [n.text for n in outline.nodes] == ["H", "one", "two", "nested"]
    assert outline.nodes[3].level == 1
    assert outline.nodes[3].parent == outline.nodes[2].id


def test_front_matter_and_fenced_code_and_hr():
    text = "---\ntitle: X\n---\n\n# H\n\n```python\ncode = 1\n```\n\n***\n\n- item\n"
    outline = parse_outline(text)
    kinds = [n.kind for n in outline.nodes]
    assert kinds == ["meta", "heading", "paragraph", "item"]
    assert not outline.nodes[2].coverable  # fenced code is not coverable
    assert "code = 1" in outline.nodes[2].text_md


def test_continuation_line_joins_the_item():
    outline = parse_outline("- first line\n  continued here\n\n- second\n")
    assert outline.nodes[0].text == "first line continued here"
    assert outline.nodes[0].line_end == 2
    assert outline.nodes[1].text == "second"


def test_paragraph_lines_join_until_blank():
    outline = parse_outline("## H\n\none\ntwo\n\nthree\n")
    paragraphs = [n for n in outline.nodes if n.kind == "paragraph"]
    assert [p.text for p in paragraphs] == ["one two", "three"]


@pytest.mark.parametrize(
    "line,expected",
    [
        ("Otázka: čo?", True),
        ("**Otázka:** čo?", True),
        ("otazka: co", True),
        ("Question: what?", True),
        ("Q: what?", True),
        ("Quick note about q", False),
        ("Questions are hard", False),
        ("no colon here", False),
    ],
)
def test_question_detection(line: str, expected: bool):
    assert is_question_line(line) is expected


def test_strip_inline_md():
    assert strip_inline_md("**bold** and *it* and `code`") == "bold and it and code"
    assert strip_inline_md("~~gone~~ [t](u)") == "gone t"


def test_normalize_drops_diacritics_and_punctuation():
    assert normalize("Psychedeliká vs. dych!") == "psychedelika vs dych"


def test_clean_text_keeps_line_count():
    src = "a\r\nb\r\n\x1b[31mc\x1b[0m\n"
    assert clean_text(src).split("\n") == ["a", "b", "c", ""]


def test_parser_never_raises_on_random_input():
    pieces = [
        "# h",
        "## h2",
        "- item",
        "\t- nested",
        "1. num",
        "> quote",
        "```",
        "---",
        "***",
        "**Meta:** v",
        "Otázka: x",
        "",
        "    indented",
        "text \x00 with \x1b[1m control",
        "| a | b |",
        "![img](x)",
        "[l](u)",
        "~~~",
        "\ttab item",
        "***bold italic***",
    ]
    rng = random.Random(20260903)
    for _ in range(200):
        text = "\n".join(rng.choice(pieces) for _ in range(rng.randint(1, 40)))
        outline = parse_outline(text)
        assert all(n.id.startswith("T") for n in outline.nodes)


def test_source_file_is_never_opened_for_writing(osnova_path: Path, monkeypatch):
    real_open = Path.open

    def guard(self, mode="r", *args, **kwargs):
        if self == osnova_path and any(c in str(mode) for c in "wa+x"):
            raise AssertionError("the outline must never be opened for writing")
        return real_open(self, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guard)
    from livecaster.outline.parser import parse_outline_file

    parse_outline_file(osnova_path)
