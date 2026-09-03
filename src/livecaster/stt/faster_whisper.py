"""faster-whisper — the Linux fallback with a forceable language."""

from __future__ import annotations

import numpy as np

from livecaster.log import get_logger
from livecaster.session.models import Word
from livecaster.stt.base import EngineUnavailable, STTResult
from livecaster.stt.postprocess import clean

log = get_logger(__name__)

DEFAULT_MODEL = "large-v3-turbo"


class FasterWhisperEngine:
    name = "faster-whisper"
    languages = None  # open set
    can_force_language = True

    def __init__(
        self,
        model: str = "",
        device: str = "auto",
        compute_type: str = "default",
        cpu_threads: int = 0,
    ) -> None:
        self.model_id = model or DEFAULT_MODEL
        self.device = device
        self.compute_type = compute_type
        self.cpu_threads = cpu_threads
        self._model = None

    def warmup(self) -> None:
        if self._model is not None:
            return
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:  # pragma: no cover - platform dependent
            raise EngineUnavailable(
                "faster-whisper is not installed. Run `uv sync --extra linux` (or --extra cuda)."
            ) from exc
        log.info("loading %s", self.model_id)
        self._model = WhisperModel(
            self.model_id,
            device=self.device,
            compute_type=self.compute_type,
            cpu_threads=self.cpu_threads,
        )
        self.transcribe(np.zeros(16_000, dtype=np.float32), None)

    def transcribe(self, audio: np.ndarray, language: str | None) -> STTResult:
        if self._model is None:
            self.warmup()
        assert self._model is not None
        lang = language if language and language != "auto" else None
        try:
            segments, info = self._model.transcribe(
                audio.astype(np.float32), language=lang, word_timestamps=True, vad_filter=False
            )
            segments = list(segments)
        except Exception as exc:  # pragma: no cover - runtime failure path
            raise EngineUnavailable(f"faster-whisper failed: {exc}") from exc
        words: list[Word] = []
        parts: list[str] = []
        for seg in segments:
            parts.append(seg.text)
            for w in getattr(seg, "words", None) or []:
                text = (w.word or "").strip()
                if text:
                    words.append(Word(start=float(w.start), end=float(w.end), text=text))
        return STTResult(
            text=clean(" ".join(parts)),
            language=getattr(info, "language", None),
            words=words,
        )
