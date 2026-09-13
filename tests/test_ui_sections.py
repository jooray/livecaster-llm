"""Section splitting for the Result panel, where each ## is a click-to-copy block.

The function is lifted out of `app.js` and run on its own under node: it is pure by
design, so there is no DOM to stand up. The slice relies on the file's indentation —
a top-level function inside the IIFE closes with a brace in column 2, and nothing
inside the body does.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

APP_JS = Path(__file__).resolve().parents[1] / "src" / "livecaster" / "ui" / "app.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def source_of(name: str) -> str:
    src = APP_JS.read_text(encoding="utf-8")
    start = src.index(f"function {name}(")
    end = src.index("\n  }\n", start)
    return src[start : end + len("\n  }")]


def split(markdown: str) -> list[dict]:
    script = f"""
    const fn = eval('(' + {json.dumps(source_of("splitSections"))} + ')');
    let src = '';
    process.stdin.on('data', (d) => (src += d));
    process.stdin.on('end', () => process.stdout.write(JSON.stringify(fn(src))));
    """
    out = subprocess.run(["node", "-e", script], input=markdown, capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


SK_NOTES = """# Dych a telo

_01:02:03_

## Zhrnutie

Prvý riadok.
Druhý riadok.

## Sociálny príspevok

Nový diel je vonku.

## Zmienky

### 📚 Knihy

- **Breath** — James Nestor

### 🎧 Podcasty

- **Niečo iné**

## Sľuby

—
"""


def test_the_title_and_its_meta_line_are_a_lead_not_a_section():
    blocks = split(SK_NOTES)
    assert blocks[0]["level"] == 0
    assert blocks[0]["text"] == ""
    assert "# Dych a telo" in blocks[0]["body"]


def test_sections_are_found_by_level_not_by_english_words():
    blocks = split(SK_NOTES)
    assert [b["heading"] for b in blocks if b["level"] == 2] == [
        "Zhrnutie",
        "Sociálny príspevok",
        "Zmienky",
        "Sľuby",
    ]


def test_the_heading_is_not_part_of_what_gets_copied():
    social = next(b for b in split(SK_NOTES) if b["heading"] == "Sociálny príspevok")
    assert social["text"] == "Nový diel je vonku."


def test_subsections_nest_under_their_section():
    mentions = next(b for b in split(SK_NOTES) if b["heading"] == "Zmienky")
    assert [c["heading"] for c in mentions["children"]] == ["📚 Knihy", "🎧 Podcasty"]
    assert [c["level"] for c in mentions["children"]] == [3, 3]


def test_a_section_copies_its_subsections_too():
    mentions = next(b for b in split(SK_NOTES) if b["heading"] == "Zmienky")
    assert mentions["text"].startswith("### 📚 Knihy")
    assert "James Nestor" in mentions["text"]
    assert "Niečo iné" in mentions["text"]
    # ...while the group on its own copies only itself.
    assert "Niečo iné" not in mentions["children"][0]["text"]


def test_a_section_renders_only_what_it_holds_itself():
    mentions = next(b for b in split(SK_NOTES) if b["heading"] == "Zmienky")
    assert mentions["body"] == ""


def test_a_fenced_block_cannot_open_a_section():
    blocks = split("## Prepis\n\n```\n## nie nadpis\n### ani toto\n```\n\nKoniec.\n")
    assert [b["heading"] for b in blocks] == ["Prepis"]
    assert "## nie nadpis" in blocks[0]["text"]


def test_a_tilde_fence_closes_only_on_tildes():
    blocks = split("## A\n\n~~~\n```\n## hidden\n~~~\n\n## B\n\nx\n")
    assert [b["heading"] for b in blocks] == ["A", "B"]


def test_a_document_without_sections_is_one_lead_block():
    blocks = split("# Transcript\n\n**[00:00:01] Host**\n\nAhoj.\n")
    assert len(blocks) == 1
    assert blocks[0]["level"] == 0


def test_an_empty_document_yields_nothing():
    assert split("") == []


def test_a_closing_hash_sequence_is_not_part_of_the_heading():
    assert split("## Zhrnutie ##\n\nx\n")[0]["heading"] == "Zhrnutie"


def test_an_empty_section_has_nothing_to_copy():
    blocks = split("## Prázdne\n\n## Plné\n\nx\n")
    assert blocks[0]["text"] == ""
    assert blocks[1]["text"] == "x"


def test_a_subsection_without_a_section_stands_on_its_own():
    blocks = split("### Sama\n\nx\n")
    assert [(b["level"], b["heading"]) for b in blocks] == [(3, "Sama")]
