"""Silero VAD wrapper (pysilero-vad, onnxruntime, no torch)."""

from __future__ import annotations

import numpy as np

FRAME_SAMPLES = 512


class VAD:
    """Speech probability for one 512-sample (32 ms) frame at 16 kHz."""

    def __init__(self) -> None:
        from pysilero_vad import SileroVoiceActivityDetector

        self._vad = SileroVoiceActivityDetector()

    def reset(self) -> None:
        try:
            self._vad.reset()
        except AttributeError:  # pragma: no cover - older pysilero
            pass

    def __call__(self, frame: np.ndarray) -> float:
        if frame.size != FRAME_SAMPLES:
            if frame.size < FRAME_SAMPLES:
                frame = np.concatenate([frame, np.zeros(FRAME_SAMPLES - frame.size, dtype=np.float32)])
            else:
                frame = frame[:FRAME_SAMPLES]
        pcm = np.clip(frame, -1.0, 1.0)
        pcm16 = (pcm * 32767.0).astype("<i2").tobytes()
        return float(self._vad(pcm16))


class EnergyVAD:
    """Deterministic fallback used by tests and when onnxruntime is unavailable."""

    def __init__(self, threshold: float = 0.02) -> None:
        self.threshold = threshold

    def reset(self) -> None:
        pass

    def __call__(self, frame: np.ndarray) -> float:
        rms = float(np.sqrt(np.mean(np.square(frame))) if frame.size else 0.0)
        return min(1.0, rms / self.threshold) if self.threshold > 0 else 0.0


def make_vad(kind: str = "silero") -> VAD | EnergyVAD:
    if kind == "energy":
        return EnergyVAD()
    try:
        return VAD()
    except Exception:  # pragma: no cover - missing model or onnxruntime
        return EnergyVAD()
