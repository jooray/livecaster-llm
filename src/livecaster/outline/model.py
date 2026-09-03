"""Outline node model (SPEC §7.1)."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, PrivateAttr
from unidecode import unidecode

NodeKind = Literal["heading", "item", "paragraph", "question", "meta"]

_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_WS_RE = re.compile(r"\s+")


def normalize(s: str) -> str:
    """Casefold, drop diacritics and punctuation, collapse whitespace (PLAN §5.7)."""
    s = unidecode(s).casefold()
    s = _PUNCT_RE.sub(" ", s)
    return _WS_RE.sub(" ", s).strip()


class Node(BaseModel):
    id: str
    kind: NodeKind
    level: int = 0
    parent: str | None = None
    children: list[str] = Field(default_factory=list)
    text_md: str = ""
    text: str = ""
    links: list[str] = Field(default_factory=list)
    line_start: int = 0
    line_end: int = 0
    coverable: bool = False


class Outline(BaseModel):
    """Nodes in document order plus a few convenience lookups."""

    nodes: list[Node] = Field(default_factory=list)
    source_path: str | None = None
    next_id: int = 1

    _index: dict[str, Node] = PrivateAttr(default_factory=dict)

    def model_post_init(self, __context: object) -> None:
        self.reindex()

    def reindex(self) -> None:
        self._index = {n.id: n for n in self.nodes}

    @property
    def index(self) -> dict[str, Node]:
        if len(self._index) != len(self.nodes):
            self.reindex()
        return self._index

    def get(self, node_id: str) -> Node | None:
        return self.index.get(node_id)

    def __contains__(self, node_id: object) -> bool:
        return node_id in self.index

    def __len__(self) -> int:
        return len(self.nodes)

    def leaves(self) -> list[Node]:
        """Coverable nodes, in document order."""
        return [n for n in self.nodes if n.coverable]

    def leaf_ids(self) -> list[str]:
        return [n.id for n in self.nodes if n.coverable]

    def children_of(self, node_id: str) -> list[Node]:
        node = self.get(node_id)
        if node is None:
            return []
        return [self.index[c] for c in node.children if c in self.index]

    def descendants_of(self, node_id: str) -> list[Node]:
        out: list[Node] = []
        stack = list(self.children_of(node_id))
        while stack:
            n = stack.pop(0)
            out.append(n)
            stack = list(self.children_of(n.id)) + stack
        return out

    def coverable_leaves_under(self, node_id: str) -> list[Node]:
        """Coverable descendants of a node; the node itself if it is a coverable leaf."""
        node = self.get(node_id)
        if node is None:
            return []
        desc = [n for n in self.descendants_of(node_id) if n.coverable]
        if desc:
            return desc
        return [node] if node.coverable else []

    def ancestors_of(self, node_id: str) -> list[Node]:
        out: list[Node] = []
        node = self.get(node_id)
        while node is not None and node.parent:
            parent = self.get(node.parent)
            if parent is None:
                break
            out.append(parent)
            node = parent
        return out

    def section_of(self, node_id: str) -> Node | None:
        """Nearest heading ancestor, or the node itself when it is a heading."""
        node = self.get(node_id)
        if node is None:
            return None
        if node.kind == "heading":
            return node
        for a in self.ancestors_of(node_id):
            if a.kind == "heading":
                return a
        return None
