"""Whisper large-v3-turbo through MLX — the macOS fallback with forceable language."""

from __future__ import annotations

import numpy as np

from livecaster.log import get_logger
from livecaster.session.models import Word
from livecaster.stt.base import EngineUnavailable, STTResult
from livecaster.stt.postprocess import clean

log = get_logger(__name__)

DEFAULT_MODEL = "mlx-community/whisper-large-v3-turbo"


class WhisperMLXEngine:
    name = "whisper-mlx"
    languages = None  # open set
    can_force_language = True

    def __init__(self, model: str = "") -> None:
        self.model_id = model or DEFAULT_MODEL
        self._transcribe = None

    def warmup(self) -> None:
        if self._transcribe is not None:
            return
        try:
            import mlx_whisper
        except ImportError as exc:  # pragma: no cover - platform dependent
            raise EngineUnavailable(
                "mlx-whisper is not installed. Run `uv sync --extra mac-whisper` — note that it "
                "pulls in PyTorch, which the default macOS install deliberately avoids (D7)."
            ) from exc
        self._transcribe = mlx_whisper.transcribe
        log.info("using %s", self.model_id)
        self.transcribe(np.zeros(16_000, dtype=np.float32), None)

    def transcribe(self, audio: np.ndarray, language: str | None) -> STTResult:
        if self._transcribe is None:
            self.warmup()
        assert self._transcribe is not None
        kwargs = {"path_or_hf_repo": self.model_id, "word_timestamps": True}
        if language and language != "auto":
            kwargs["language"] = language
        try:
            result = self._transcribe(audio.astype(np.float32), **kwargs)
        except Exception as exc:  # pragma: no cover - runtime failure path
            raise EngineUnavailable(f"mlx-whisper failed: {exc}") from exc
        words: list[Word] = []
        for seg in result.get("segments", []) or []:
            for w in seg.get("words", []) or []:
                text = (w.get("word") or "").strip()
                if text:
                    words.append(
                        Word(start=float(w.get("start", 0.0)), end=float(w.get("end", 0.0)), text=text)
                    )
        return STTResult(
            text=clean(result.get("text", "")),
            language=result.get("language"),
            words=words,
        )
