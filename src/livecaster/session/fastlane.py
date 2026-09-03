"""Fast lane: zero-cost trigger matching on every new segment (FR-16, SPEC §7.6)."""

from __future__ import annotations

import re

from rapidfuzz import fuzz
from unidecode import unidecode

from livecaster.outline.model import Outline
from livecaster.session.models import Preflight, Segment

MIN_WORD_LEN = 5
WARM_THRESHOLD = 0.5
FUZZY_RATIO = 85
MAX_NODE_WORDS = 8

STOP_WORDS = {
    # Slovak / Czech
    "ktory",
    "ktora",
    "ktore",
    "preco",
    "potom",
    "vsetko",
    "nieco",
    "trochu",
    "vlastne",
    "napriklad",
    "pretoze",
    "takze",
    "vsak",
    "este",
    "medzi",
    "kedze",
    "proste",
    "mozno",
    "prave",
    "velmi",
    "ovsem",
    "kdyz",
    "protoze",
    "vsechno",
    "nekdy",
    "jenom",
    "budeme",
    # English
    "about",
    "there",
    "these",
    "those",
    "which",
    "would",
    "could",
    "should",
    "their",
    "other",
    "after",
    "before",
    "because",
    "really",
    "something",
    "things",
    "think",
}

_WORD_RE = re.compile(r"[\w']+", re.UNICODE)


def fold(s: str) -> str:
    return unidecode(s).casefold()


def significant_words(text: str) -> list[str]:
    out: list[str] = []
    for w in _WORD_RE.findall(fold(text)):
        if len(w) >= MIN_WORD_LEN and w not in STOP_WORDS and not w.isdigit():
            out.append(w)
    return out


def _phrase_hits(phrases: list[str], folded: str, words: set[str]) -> int:
    """Count phrases present in the text, tolerating inflection but not substrings."""
    hits = 0
    for phrase in phrases:
        if " " in phrase:
            if phrase in folded or fuzz.partial_ratio(phrase, folded) >= FUZZY_RATIO:
                hits += 1
        elif phrase in words or any(fuzz.ratio(phrase, w) >= FUZZY_RATIO for w in words):
            hits += 1
    return hits


class FastLane:
    """Pre-computes per-node phrases, then scores each incoming segment against them.

    Pre-flight triggers and the node's own words are scored separately. A trigger is
    a phrase the model chose *because* it points at that one node, so a single hit is
    already a signal; the node's own words are generic, so those follow the spec's
    "half the phrases" rule. See DECISIONS.md (D6).
    """

    def __init__(self, outline: Outline, preflight: Preflight | None = None) -> None:
        self.triggers: dict[str, list[str]] = {}
        self.words: dict[str, list[str]] = {}
        self.rebuild(outline, preflight)

    @property
    def phrases(self) -> dict[str, list[str]]:
        """Everything a node can match on, for inspection and tests."""
        return {
            node_id: self.triggers.get(node_id, []) + self.words.get(node_id, [])
            for node_id in set(self.triggers) | set(self.words)
        }

    def rebuild(self, outline: Outline, preflight: Preflight | None = None) -> None:
        self.triggers = {}
        self.words = {}
        for node in outline.leaves():
            if preflight is not None:
                pf = preflight.nodes.get(node.id)
                if pf and pf.triggers:
                    self.triggers[node.id] = _unique(fold(t) for t in pf.triggers if t.strip())
            # Only the longest words: a 40-word bullet would otherwise need 20 hits
            # before it could reach the half-the-phrases threshold.
            node_words = _unique(significant_words(node.text))
            if node_words:
                self.words[node.id] = sorted(node_words, key=len, reverse=True)[:MAX_NODE_WORDS]

    def score_text(self, text: str) -> dict[str, float]:
        folded = fold(text)
        if not folded.strip():
            return {}
        words = set(_WORD_RE.findall(folded))
        out: dict[str, float] = {}
        for node_id in set(self.triggers) | set(self.words):
            score = 0.0
            triggers = self.triggers.get(node_id)
            if triggers:
                hits = _phrase_hits(triggers, folded, words)
                if hits:
                    score = max(WARM_THRESHOLD, hits / len(triggers))
            own = self.words.get(node_id)
            if own:
                word_score = _phrase_hits(own, folded, words) / len(own)
                if word_score >= WARM_THRESHOLD:
                    score = max(score, word_score)
            if score > 0.0:
                out[node_id] = round(min(1.0, score), 3)
        return out

    def score_segment(self, segment: Segment) -> dict[str, float]:
        return self.score_text(segment.text)


def _unique(items) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        item = item.strip()
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out
