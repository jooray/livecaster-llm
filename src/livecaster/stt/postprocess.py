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


# Scripts, for the language guard. Parakeet v3 cannot be told which language it is
# hearing (DECISIONS D20), and when the audio is poor it answers in the wrong one.
# Whole alphabets it never should have reached are cheap to catch and always wrong.
_SCRIPTS = {
    "cyrillic": re.compile(r"[Ѐ-ӿ]"),
    "greek": re.compile(r"[Ͱ-Ͽ]"),
    "latin": re.compile(r"[A-Za-zÀ-ɏ]"),
}

#: Which script each language Parakeet knows is actually written in.
LANGUAGE_SCRIPT = {
    "bg": "cyrillic", "ru": "cyrillic", "uk": "cyrillic", "el": "greek",
}  # fmt: skip


def script_of(text: str) -> str | None:
    """The script most of the letters in ``text`` belong to, or None if it is a wash."""
    counts = {name: len(rx.findall(text)) for name, rx in _SCRIPTS.items()}
    total = sum(counts.values())
    if total < 3:
        return None
    name, best = max(counts.items(), key=lambda kv: kv[1])
    return name if best / total > 0.6 else None


def wrong_script(text: str, language: str | None) -> bool:
    """True when ``text`` is written in an alphabet ``language`` never uses."""
    if not language or language == "auto":
        return False
    expected = LANGUAGE_SCRIPT.get(language.lower(), "latin")
    found = script_of(text)
    return found is not None and found != expected
