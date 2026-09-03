"""Markdown outline parser (SPEC §7.1).

The parser is deliberately forgiving: it must never raise on arbitrary text.
"""

from __future__ import annotations

import re
from pathlib import Path

from livecaster.outline.model import Node, Outline

# --- line classifiers -------------------------------------------------------

RE_HR = re.compile(r"^\s*(-{3,}|\*{3,}|_{3,})\s*$")
RE_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
RE_FENCE = re.compile(r"^\s*(```+|~~~+)")
RE_LIST = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$")
RE_QUESTION = re.compile(r"^\**\s*(ot[áa]zka|question|q)\**\s*:\s*\**\s*", re.IGNORECASE)
RE_META_BOLD = re.compile(r"^\*\*[^*]+:\*\*")
RE_META_PLAIN = re.compile(r"^[\w\s]+:\s")
RE_FRONT_MATTER = re.compile(r"^---\s*$")

# --- cleaning ---------------------------------------------------------------

RE_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
RE_C0 = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# --- inline markdown --------------------------------------------------------

RE_MD_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
RE_MD_LINK = re.compile(r"\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
RE_MD_AUTOLINK = re.compile(r"<((?:https?|mailto):[^>]+)>")
RE_BARE_URL = re.compile(r"(?<![\(<])\bhttps?://[^\s<>\)\]]+")
RE_MD_CODE = re.compile(r"`+([^`]*)`+")
RE_MD_BOLD = re.compile(r"(\*\*|__)(.+?)\1", re.DOTALL)
RE_MD_ITALIC = re.compile(r"(?<![\w*_])([*_])(?!\s)(.+?)(?<!\s)\1(?![\w*_])", re.DOTALL)
RE_MD_STRIKE = re.compile(r"~~(.+?)~~", re.DOTALL)
RE_WS = re.compile(r"\s+")

TAB_WIDTH = 4


def clean_text(text: str) -> str:
    """Normalize line endings, expand tabs for indentation, drop control characters."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = RE_ANSI.sub("", text)
    out_lines = []
    for line in text.split("\n"):
        # Tabs count as 4 spaces, but only for leading indentation.
        stripped = line.lstrip("\t ")
        indent_src = line[: len(line) - len(stripped)]
        indent = indent_src.replace("\t", " " * TAB_WIDTH)
        out_lines.append(indent + RE_C0.sub("", stripped))
    return "\n".join(out_lines)


def strip_inline_md(s: str) -> str:
    """Plain single-line text from inline Markdown."""
    s = RE_MD_IMAGE.sub(r"\1", s)
    s = RE_MD_LINK.sub(r"\1", s)
    s = RE_MD_AUTOLINK.sub(r"\1", s)
    s = RE_MD_CODE.sub(r"\1", s)
    s = RE_MD_STRIKE.sub(r"\1", s)
    s = RE_MD_BOLD.sub(r"\2", s)
    s = RE_MD_ITALIC.sub(r"\2", s)
    return RE_WS.sub(" ", s).strip()


def extract_links(s: str) -> list[str]:
    links: list[str] = []
    for m in RE_MD_LINK.finditer(s):
        links.append(m.group(2))
    for m in RE_MD_AUTOLINK.finditer(s):
        links.append(m.group(1))
    for m in RE_BARE_URL.finditer(s):
        url = m.group(0).rstrip(".,;:!?")
        if url not in links:
            links.append(url)
    seen: set[str] = set()
    out: list[str] = []
    for url in links:
        if url not in seen:
            seen.add(url)
            out.append(url)
    return out


def is_question_line(s: str) -> bool:
    return bool(RE_QUESTION.match(s.strip()))


class _Builder:
    def __init__(self, start_id: int = 1) -> None:
        self.nodes: list[Node] = []
        self.next_id = start_id

    def add(
        self,
        kind: str,
        *,
        level: int,
        text_md: str,
        line_start: int,
        parent: Node | None,
        coverable: bool,
    ) -> Node:
        node = Node(
            id=f"T{self.next_id}",
            kind=kind,  # type: ignore[arg-type]
            level=level,
            parent=parent.id if parent else None,
            text_md=text_md,
            text=strip_inline_md(text_md),
            links=extract_links(text_md),
            line_start=line_start,
            line_end=line_start,
            coverable=coverable,
        )
        self.next_id += 1
        self.nodes.append(node)
        if parent is not None:
            parent.children.append(node.id)
        return node


def parse_outline(text: str, source_path: str | None = None, start_id: int = 1) -> Outline:
    """Parse Markdown into an :class:`Outline`. Never raises on arbitrary input."""
    raw_lines = clean_text(text).split("\n")
    b = _Builder(start_id)

    heading_stack: list[Node] = []  # innermost last
    item_stack: list[tuple[int, Node]] = []  # (indent, node)
    current_para: Node | None = None
    current_item: Node | None = None
    seen_h2 = False
    in_fence = False
    fence_marker = ""
    fence_node: Node | None = None
    in_front_matter = False

    def current_heading() -> Node | None:
        return heading_stack[-1] if heading_stack else None

    def close_blocks() -> None:
        nonlocal current_para, current_item
        current_para = None
        current_item = None

    for i, line in enumerate(raw_lines):
        lineno = i + 1
        stripped = line.strip()

        # --- YAML front matter (only when the file opens with a fence) ------
        if lineno == 1 and RE_FRONT_MATTER.match(line):
            in_front_matter = True
            continue
        if in_front_matter:
            if RE_FRONT_MATTER.match(line):
                in_front_matter = False
            elif stripped:
                node = b.add(
                    "meta",
                    level=0,
                    text_md=stripped,
                    line_start=lineno,
                    parent=None,
                    coverable=False,
                )
                node.line_end = lineno
            continue

        # --- fenced code ----------------------------------------------------
        if in_fence:
            assert fence_node is not None
            fence_node.text_md += "\n" + line
            fence_node.line_end = lineno
            if stripped.startswith(fence_marker):
                in_fence = False
                fence_node.text = strip_inline_md(fence_node.text_md.replace("\n", " "))
                fence_node = None
            continue
        fm = RE_FENCE.match(line)
        if fm:
            close_blocks()
            in_fence = True
            fence_marker = fm.group(1)[:3]
            fence_node = b.add(
                "paragraph",
                level=0,
                text_md=line,
                line_start=lineno,
                parent=current_heading(),
                coverable=False,
            )
            fence_node.line_end = lineno
            continue

        # --- blank line -----------------------------------------------------
        if not stripped:
            close_blocks()
            continue

        # --- horizontal rule ------------------------------------------------
        if RE_HR.match(line):
            close_blocks()
            continue

        # --- heading --------------------------------------------------------
        hm = RE_HEADING.match(line)
        if hm:
            close_blocks()
            item_stack.clear()
            level = len(hm.group(1))
            if level >= 2:
                seen_h2 = True
            while heading_stack and heading_stack[-1].level >= level:
                heading_stack.pop()
            node = b.add(
                "heading",
                level=level,
                text_md=hm.group(2).strip(),
                line_start=lineno,
                parent=current_heading(),
                coverable=False,  # decided after the whole tree is known
            )
            node.line_end = lineno
            heading_stack.append(node)
            continue

        # --- list item ------------------------------------------------------
        lm = RE_LIST.match(line)
        if lm:
            current_para = None
            indent = len(lm.group(1))
            body = lm.group(3).strip()
            while item_stack and indent <= item_stack[-1][0]:
                item_stack.pop()
            if item_stack:
                parent_node = item_stack[-1][1]
                depth = parent_node.level + 1
            else:
                parent_node = current_heading()
                depth = 0
            kind = "question" if is_question_line(body) else "item"
            node = b.add(
                kind,
                level=depth,
                text_md=body,
                line_start=lineno,
                parent=parent_node,
                coverable=True,
            )
            node.line_end = lineno
            item_stack.append((indent, node))
            current_item = node
            continue

        indent = len(line) - len(line.lstrip(" "))

        # --- question line at line start ------------------------------------
        if is_question_line(stripped) and indent == 0:
            close_blocks()
            item_stack.clear()
            node = b.add(
                "question",
                level=0,
                text_md=stripped,
                line_start=lineno,
                parent=current_heading(),
                coverable=True,
            )
            node.line_end = lineno
            continue

        # --- metadata line (only before the first level-2 heading) ----------
        if not seen_h2 and (RE_META_BOLD.match(stripped) or RE_META_PLAIN.match(stripped)):
            close_blocks()
            node = b.add(
                "meta",
                level=0,
                text_md=stripped,
                line_start=lineno,
                parent=current_heading(),
                coverable=False,
            )
            node.line_end = lineno
            continue

        # --- continuation of the previous list item -------------------------
        if current_item is not None and indent > 0:
            current_item.text_md += " " + stripped
            current_item.text = strip_inline_md(current_item.text_md)
            current_item.links = extract_links(current_item.text_md)
            current_item.line_end = lineno
            continue

        # --- paragraph ------------------------------------------------------
        if current_para is not None:
            current_para.text_md += " " + stripped
            current_para.text = strip_inline_md(current_para.text_md)
            current_para.links = extract_links(current_para.text_md)
            current_para.line_end = lineno
            continue

        close_blocks()
        item_stack.clear()
        node = b.add(
            "paragraph",
            level=0,
            text_md=stripped,
            line_start=lineno,
            parent=current_heading(),
            coverable=True,
        )
        node.line_end = lineno
        current_para = node

    outline = Outline(nodes=b.nodes, source_path=source_path, next_id=b.next_id)
    _mark_childless_headings(outline)
    return outline


def _mark_childless_headings(outline: Outline) -> None:
    """A heading is coverable only when nothing coverable sits underneath it (FR-02)."""
    for node in outline.nodes:
        if node.kind != "heading":
            continue
        node.coverable = not any(d.coverable for d in outline.descendants_of(node.id))


def parse_outline_file(path: str | Path) -> Outline:
    """Read and parse an outline file. Opened read-only, always."""
    p = Path(path)
    raw = p.read_bytes().decode("utf-8", errors="replace")
    return parse_outline(raw, source_path=str(p))
