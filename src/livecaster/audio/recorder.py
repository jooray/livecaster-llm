"""Per-channel WAV backup writer (FR-09)."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import numpy as np

SAMPLE_RATE = 16_000


class WavRecorder:
    def __init__(self, path: str | Path, samplerate: int = SAMPLE_RATE) -> None:
        self.path = Path(path)
        self.samplerate = samplerate
        self._file = None
        self._lock = threading.Lock()
        self._last_flush = time.monotonic()
        self.frames_written = 0

    @staticmethod
    def unique_path(directory: Path, channel: str) -> Path:
        """`host.wav`, then `host.2.wav` on resume."""
        base = directory / f"{channel}.wav"
        if not base.exists():
            return base
        n = 2
        while (directory / f"{channel}.{n}.wav").exists():
            n += 1
        return directory / f"{channel}.{n}.wav"

    def open(self) -> None:
        import soundfile as sf

        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = sf.SoundFile(
            str(self.path), mode="w", samplerate=self.samplerate, channels=1, subtype="PCM_16"
        )

    def write(self, frame: np.ndarray) -> None:
        if self._file is None:
            return
        with self._lock:
            self._file.write(frame)
            self.frames_written += frame.size
            now = time.monotonic()
            if now - self._last_flush >= 1.0:
                self._file.flush()
                self._last_flush = now

    def close(self) -> None:
        with self._lock:
            if self._file is not None:
                try:
                    self._file.flush()
                    self._file.close()
                finally:
                    self._file = None

    @property
    def seconds(self) -> float:
        return self.frames_written / self.samplerate
