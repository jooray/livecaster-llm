"""A silent engine for tests and for replaying transcripts without audio."""

from __future__ import annotations

import numpy as np

from livecaster.stt.base import STTResult


class MockEngine:
    name = "mock"
    languages = None

    def __init__(self, texts: list[str] | None = None, language: str | None = "sk") -> None:
        self.texts = list(texts or [])
        self.language = language
        self._i = 0

    def warmup(self) -> None:
        return None

    def transcribe(self, audio: np.ndarray, language: str | None) -> STTResult:
        if not self.texts:
            return STTResult(text="", language=self.language)
        text = self.texts[self._i % len(self.texts)]
        self._i += 1
        return STTResult(text=text, language=self.language)
