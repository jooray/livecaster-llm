"""Parakeet TDT 0.6B v3 through MLX — the macOS default (SPEC §7.3)."""

from __future__ import annotations

import numpy as np

from livecaster.log import get_logger
from livecaster.session.models import Word
from livecaster.stt.base import EngineUnavailable, STTResult
from livecaster.stt.postprocess import clean

log = get_logger(__name__)

DEFAULT_MODEL = "mlx-community/parakeet-tdt-0.6b-v3"

# The 25 European languages of Parakeet v3.
LANGUAGES = {
    "bg",
    "hr",
    "cs",
    "da",
    "nl",
    "en",
    "et",
    "fi",
    "fr",
    "de",
    "el",
    "hu",
    "it",
    "lv",
    "lt",
    "mt",
    "pl",
    "pt",
    "ro",
    "sk",
    "sl",
    "es",
    "sv",
    "ru",
    "uk",
}


class ParakeetMLXEngine:
    name = "parakeet-mlx"
    languages = LANGUAGES

    def __init__(self, model: str = "") -> None:
        self.model_id = model or DEFAULT_MODEL
        self._model = None

    def warmup(self) -> None:
        if self._model is not None:
            return
        try:
            from parakeet_mlx import from_pretrained
        except ImportError as exc:  # pragma: no cover - platform dependent
            raise EngineUnavailable(
                "parakeet-mlx is not installed. Run `uv sync --extra mac` on Apple Silicon."
            ) from exc
        log.info("loading %s", self.model_id)
        self._model = from_pretrained(self.model_id)
        self.transcribe(np.zeros(16_000, dtype=np.float32), None)

    def transcribe(self, audio: np.ndarray, language: str | None) -> STTResult:
        # Parakeet v3 auto-detects among its 25 languages and cannot be forced.
        if self._model is None:
            self.warmup()
        assert self._model is not None
        try:
            import mlx.core as mx

            result = self._model.transcribe(mx.array(audio.astype(np.float32)))
        except Exception as exc:  # pragma: no cover - runtime failure path
            raise EngineUnavailable(f"parakeet-mlx failed: {exc}") from exc
        text = clean(getattr(result, "text", "") or "")
        words: list[Word] = []
        for sentence in getattr(result, "sentences", []) or []:
            for tok in getattr(sentence, "tokens", []) or []:
                token_text = (getattr(tok, "text", "") or "").strip()
                if not token_text:
                    continue
                words.append(
                    Word(
                        start=float(getattr(tok, "start", 0.0)),
                        end=float(getattr(tok, "end", 0.0)),
                        text=token_text,
                    )
                )
        return STTResult(text=text, language=getattr(result, "language", None), words=words)
