"""Outline renderers: the compact block for the LLM and the annotated Markdown export."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from livecaster.outline.model import Node, Outline
from livecaster.timeutil import fmt_hms

INDENT = "  "


def _prefix(node: Node) -> str:
    if node.kind == "heading":
        return "#" * node.level + " "
    if node.kind == "meta":
        return "meta "
    if node.kind == "paragraph":
        return "¶ "
    return "- "


def render_for_llm(outline: Outline) -> str:
    """Compact one-node-per-line block (SPEC §7.1).

    Contains no status information so it stays byte-identical across ticks and
    Venice's prefix cache keeps hitting.
    """
    lines: list[str] = []
    for node in outline.nodes:
        if node.kind == "heading":
            indent = ""
        elif node.kind == "meta":
            indent = ""
        else:
            indent = INDENT * (node.level + 1)
        text = node.text_md if node.kind != "paragraph" else node.text
        text = " ".join(text.split())
        lines.append(f"[{node.id}] {indent}{_prefix(node)}{text}".rstrip())
    return "\n".join(lines)


def coverage_fraction(outline: Outline, node_id: str, states: Mapping[str, Any]) -> tuple[int, int]:
    """(covered, total) over the coverable leaves under a heading."""
    leaves = outline.coverable_leaves_under(node_id)
    total = len(leaves)
    covered = 0
    for leaf in leaves:
        st = states.get(leaf.id)
        status = getattr(st, "status", None) if st is not None else None
        if status is None and isinstance(st, dict):
            status = st.get("status")
        if status in ("covered", "skipped"):
            covered += 1
    return covered, total


def _status_of(states: Mapping[str, Any], node_id: str) -> tuple[str, float | None]:
    st = states.get(node_id)
    if st is None:
        return "untouched", None
    if isinstance(st, dict):
        return st.get("status", "untouched"), st.get("covered_at")
    return getattr(st, "status", "untouched"), getattr(st, "covered_at", None)


def render_annotated(
    outline: Outline,
    states: Mapping[str, Any],
    original_text: str,
    *,
    retired: Mapping[str, Any] | None = None,
    new_topics: Sequence[Any] | None = None,
    sync_offset: float = 0.0,
) -> str:
    """Reproduce the source file line by line, adding status markers.

    With no states set the output is byte-identical to the input.
    """
    text = original_text.replace("\r\n", "\n").replace("\r", "\n")
    had_trailing_newline = text.endswith("\n")
    lines = text.split("\n")
    if had_trailing_newline:
        lines = lines[:-1]

    # line number (1-based) -> annotation instructions
    first_line: dict[int, Node] = {}
    last_line: dict[int, Node] = {}
    for node in outline.nodes:
        if node.kind == "meta":
            continue
        first_line.setdefault(node.line_start, node)
        last_line[node.line_end] = node

    out: list[str] = []
    for idx, line in enumerate(lines):
        lineno = idx + 1
        node = first_line.get(lineno)
        end_node = last_line.get(lineno)
        new_line = line

        if node is not None and node.coverable:
            status, covered_at = _status_of(states, node.id)
            if status == "covered" and node is end_node:
                new_line = _wrap_strike_single(new_line)
            elif status == "covered":
                new_line = _wrap_strike_open(new_line)

        if end_node is not None and end_node.coverable:
            status, covered_at = _status_of(states, end_node.id)
            if status == "covered":
                if end_node is not first_line.get(end_node.line_start) or end_node.line_start != lineno:
                    new_line = new_line.rstrip() + "~~"
                t = None if covered_at is None else max(0.0, covered_at - sync_offset)
                new_line = new_line.rstrip() + f" ✅ {fmt_hms(t)}"
            elif status == "touched":
                new_line = new_line.rstrip() + " ◐"
            elif status == "skipped":
                new_line = new_line.rstrip() + " ⏭"

        if node is not None and node.kind == "heading":
            covered, total = coverage_fraction(outline, node.id, states)
            if total and covered:
                new_line = new_line.rstrip() + f" ({covered}/{total})"

        out.append(new_line)

    body = "\n".join(out)
    if had_trailing_newline:
        body += "\n"

    extras = _render_extras(outline, states, retired, new_topics)
    if extras:
        if not body.endswith("\n"):
            body += "\n"
        body += extras
    return body


def _wrap_strike_single(line: str) -> str:
    """Wrap a single line's content in ~~…~~, keeping bullet/heading markers outside."""
    stripped = line.lstrip()
    indent = line[: len(line) - len(stripped)]
    marker = ""
    for prefix_re in ("- ", "* ", "+ "):
        if stripped.startswith(prefix_re):
            marker, stripped = prefix_re, stripped[2:]
            break
    else:
        import re

        m = re.match(r"^(\d+[.)]\s+|#{1,6}\s+)", stripped)
        if m:
            marker, stripped = m.group(1), stripped[m.end() :]
    return f"{indent}{marker}~~{stripped.rstrip()}~~"


def _wrap_strike_open(line: str) -> str:
    """Opening half of a multi-line strikethrough."""
    stripped = line.lstrip()
    indent = line[: len(line) - len(stripped)]
    marker = ""
    for prefix_re in ("- ", "* ", "+ "):
        if stripped.startswith(prefix_re):
            marker, stripped = prefix_re, stripped[2:]
            break
    return f"{indent}{marker}~~{stripped}"


def _render_extras(
    outline: Outline,
    states: Mapping[str, Any],
    retired: Mapping[str, Any] | None,
    new_topics: Sequence[Any] | None,
) -> str:
    parts: list[str] = []
    if new_topics:
        rows = []
        for t in new_topics:
            title = t.get("title") if isinstance(t, dict) else getattr(t, "title", "")
            summary = t.get("summary") if isinstance(t, dict) else getattr(t, "summary", "")
            rows.append(f"- **{title}** — {summary}" if summary else f"- **{title}**")
        parts.append("\n## Témy mimo osnovy / Topics outside the outline\n\n" + "\n".join(rows) + "\n")
    if retired:
        rows = []
        for node_id, st in retired.items():
            status, _ = _status_of({node_id: st}, node_id)
            rows.append(f"- `{node_id}` ({status})")
        if rows:
            parts.append("\n## Removed from the outline during the session\n\n" + "\n".join(rows) + "\n")
    return "".join(parts)


def main() -> None:  # pragma: no cover - developer convenience
    import sys

    from livecaster.outline.parser import parse_outline_file

    if len(sys.argv) < 2:
        print("usage: python -m livecaster.outline.render <outline.md>", file=sys.stderr)
        raise SystemExit(2)
    outline = parse_outline_file(sys.argv[1])
    print(render_for_llm(outline))


if __name__ == "__main__":  # pragma: no cover
    main()
