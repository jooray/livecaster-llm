"""Transcript text clean-up: fillers and known hallucinations (SPEC §7.3)."""

from __future__ import annotations

import re

FILLERS = {
    "hm",
    "hmm",
    "hmmm",
    "mhm",
    "mhmm",
    "ehm",
    "ehmm",
    "uh",
    "uhh",
    "um",
    "umm",
    "eh",
    "ah",
    "aha",
    "mm",
    "mmm",
    "hej",
    "no",
}

HALLUCINATION_PATTERNS = [
    re.compile(r"titulky\s+(vytvo[rř]il|z\s+odposlechu)", re.IGNORECASE),
    re.compile(r"p[rř]epis\s+titulk[uů]", re.IGNORECASE),
    re.compile(r"subtitles?\s+by\s+", re.IGNORECASE),
    re.compile(r"amara\.org", re.IGNORECASE),
    re.compile(r"thank(s| you) for watching", re.IGNORECASE),
    re.compile(r"^\s*\[?\s*(hudba|music|applause|potlesk)\s*\]?\s*$", re.IGNORECASE),
    re.compile(r"ďakujem za pozretie", re.IGNORECASE),
]

_WS = re.compile(r"\s+")
_WORD = re.compile(r"[\w']+", re.UNICODE)


def collapse_whitespace(text: str) -> str:
    return _WS.sub(" ", text).strip()


def is_filler_only(text: str) -> bool:
    words = [w.casefold() for w in _WORD.findall(text)]
    return bool(words) and all(w in FILLERS for w in words)


def has_repetition(text: str, times: int = 3) -> bool:
    """A phrase of 1..5 words repeated ``times`` or more in a row is a hallucination."""
    words = text.split()
    if len(words) < times:
        return False
    for size in range(1, 6):
        if len(words) < size * times:
            continue
        for start in range(len(words) - size * times + 1):
            first = [w.casefold() for w in words[start : start + size]]
            if all(
                [w.casefold() for w in words[start + size * k : start + size * (k + 1)]] == first
                for k in range(1, times)
            ):
                return True
    return False


def is_hallucination(text: str) -> bool:
    return any(p.search(text) for p in HALLUCINATION_PATTERNS) or has_repetition(text)


def clean(text: str) -> str:
    """Return cleaned text, or an empty string when the segment should be dropped."""
    text = collapse_whitespace(text or "")
    if not text:
        return ""
    if is_filler_only(text):
        return ""
    if is_hallucination(text):
        return ""
    return text
