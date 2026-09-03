"""Parakeet v3 through onnxruntime — the Linux default (SPEC §7.3)."""

from __future__ import annotations

import numpy as np

from livecaster.log import get_logger
from livecaster.stt.base import EngineUnavailable, STTResult
from livecaster.stt.parakeet_mlx import LANGUAGES
from livecaster.stt.postprocess import clean

log = get_logger(__name__)

DEFAULT_MODEL = "nemo-parakeet-tdt-0.6b-v3"


class OnnxASREngine:
    name = "onnx-asr"
    languages = LANGUAGES
    can_force_language = False

    def __init__(self, model: str = "") -> None:
        self.model_id = model or DEFAULT_MODEL
        self._model = None

    def warmup(self) -> None:
        if self._model is not None:
            return
        try:
            import onnx_asr
        except ImportError as exc:  # pragma: no cover - platform dependent
            raise EngineUnavailable(
                "onnx-asr is not installed. Run `uv sync --extra linux` (or --extra cuda)."
            ) from exc
        log.info("loading %s", self.model_id)
        self._model = onnx_asr.load_model(self.model_id)
        self.transcribe(np.zeros(16_000, dtype=np.float32), None)

    def transcribe(self, audio: np.ndarray, language: str | None) -> STTResult:
        if self._model is None:
            self.warmup()
        assert self._model is not None
        try:
            result = self._model.recognize(audio.astype(np.float32), sample_rate=16_000)
        except TypeError:  # pragma: no cover - older signature
            result = self._model.recognize(audio.astype(np.float32))
        except Exception as exc:  # pragma: no cover - runtime failure path
            raise EngineUnavailable(f"onnx-asr failed: {exc}") from exc
        text = result if isinstance(result, str) else getattr(result, "text", str(result))
        return STTResult(text=clean(text), language=None, words=[])
