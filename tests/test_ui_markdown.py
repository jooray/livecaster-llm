"""The vendored Markdown renderer, which the Result panel depends on.

It shipped as inline-only with `parse` aliased to `parseInline`, so the first
real show notes rendered as one paragraph of literal `##` and `-`. These run
through node when it is available.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

VENDOR = Path(__file__).resolve().parents[1] / "src" / "livecaster" / "ui" / "vendor" / "marked.min.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def render(markdown: str, method: str = "parse") -> str:
    script = f"""
    const fs = require('fs'), vm = require('vm');
    const ctx = {{ window: {{}} }};
    vm.createContext(ctx);
    vm.runInContext(fs.readFileSync({json.dumps(str(VENDOR))}, 'utf8'), ctx);
    let src = '';
    process.stdin.on('data', (d) => (src += d));
    process.stdin.on('end', () => process.stdout.write(ctx.window.marked.{method}(src)));
    """
    out = subprocess.run(
        ["node", "-e", script], input=markdown, capture_output=True, text=True, check=True
    )
    return out.stdout


def test_headings_become_headings():
    assert render("# Dych\n\n## Zhrnutie\n") == "<h1>Dych</h1>\n<h2>Zhrnutie</h2>"


def test_bullets_become_a_list():
    html = render("- prvý\n- druhý\n")
    assert html.count("<ul>") == 1 and html.count("</ul>") == 1
    assert "<li>prvý</li>" in html and "<li>druhý</li>" in html


def test_nested_bullets_nest():
    # The annotated outline indents sub-bullets with tabs.
    html = render("- vonkajší\n\t- vnorený\n- ďalší\n")
    assert html.count("<ul>") == 2 and html.count("</ul>") == 2
    assert html.index("<li>vnorený</li>") > html.index("<li>vonkajší</li>")


def test_numbered_lists_are_ordered():
    html = render("1. prvý\n2. druhý\n")
    assert "<ol>" in html and "<ul>" not in html


def test_a_heading_that_starts_with_a_number_is_still_a_heading():
    assert render("## 1. Fyziológia dychu\n") == "<h2>1. Fyziológia dychu</h2>"


def test_paragraph_lines_keep_their_breaks():
    # The wrap-up writes one summary sentence per line.
    assert render("Prvá veta.\nDruhá veta.\n") == "<p>Prvá veta.<br>Druhá veta.</p>"


def test_a_blank_line_starts_a_new_paragraph():
    assert render("Jedna.\n\nDve.\n") == "<p>Jedna.</p>\n<p>Dve.</p>"


def test_rules_and_inline_markup_inside_blocks():
    html = render("---\n\n- `00:01` **Úvod** — ~~hotovo~~\n")
    assert "<hr>" in html
    assert "<code>00:01</code>" in html
    assert "<strong>Úvod</strong>" in html
    assert "<del>hotovo</del>" in html


def test_fenced_code_is_not_interpreted():
    html = render("```\n# not a heading\n```\n")
    assert "<pre><code># not a heading</code></pre>" in html


def test_html_in_the_source_is_escaped():
    assert "&lt;script&gt;" in render("- <script>alert(1)</script>\n")


def test_parse_inline_still_leaves_block_syntax_alone():
    # The outline map calls parseInline on a single node's text.
    assert render("**Wim Hof** vs. dych", method="parseInline") == "<strong>Wim Hof</strong> vs. dych"
