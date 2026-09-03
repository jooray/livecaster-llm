"""A silent engine for tests and for replaying transcripts without audio."""

from __future__ import annotations

import numpy as np

from livecaster.stt.base import STTResult


class MockEngine:
    name = "mock"
    languages = None
    can_force_language = True

    def __init__(self, texts: list[str] | None = None, language: str | None = "sk") -> None:
        self.texts = list(texts or [])
        self.language = language
        self._i = 0

    def warmup(self) -> None:
        return None

    def transcribe(self, audio: np.ndarray, language: str | None) -> STTResult:
        self._i += 1
        if not self.texts:
            # A silent engine would make `stt.engine = "mock"` useless for checking
            # the wiring, so say something deterministic about what it heard.
            seconds = len(audio) / 16_000
            return STTResult(text=f"mock utterance {self._i} ({seconds:.1f}s)", language=self.language)
        return STTResult(text=self.texts[(self._i - 1) % len(self.texts)], language=self.language)
