"""STT engine interface (SPEC §7.3)."""

from __future__ import annotations

from typing import Protocol

import numpy as np
from pydantic import BaseModel, Field

from livecaster.session.models import Word


class STTResult(BaseModel):
    text: str = ""
    language: str | None = None
    words: list[Word] = Field(default_factory=list)
    confidence: float | None = None


class STTEngine(Protocol):  # pragma: no cover - structural typing only
    name: str
    languages: set[str] | None

    def warmup(self) -> None: ...
    def transcribe(self, audio: np.ndarray, language: str | None) -> STTResult: ...


class EngineUnavailable(RuntimeError):
    """Raised when an engine's package or model cannot be loaded."""
