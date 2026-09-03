"""Session clock, time formatting and atomic file writes."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any


class SessionClock:
    """Monotonic clock whose zero is the moment the session started.

    ``offset`` lets a resumed session continue counting from where it stopped.
    """

    def __init__(self, offset: float = 0.0) -> None:
        self._start = time.monotonic()
        self._offset = offset
        self._paused_at: float | None = None
        self._paused_total = 0.0

    def now(self) -> float:
        if self._paused_at is not None:
            base = self._paused_at
        else:
            base = time.monotonic()
        return base - self._start - self._paused_total + self._offset

    def pause(self) -> None:
        if self._paused_at is None:
            self._paused_at = time.monotonic()

    def resume(self) -> None:
        if self._paused_at is not None:
            self._paused_total += time.monotonic() - self._paused_at
            self._paused_at = None

    @property
    def paused(self) -> bool:
        return self._paused_at is not None


class ManualClock:
    """Clock driven by hand; used by replay and tests."""

    def __init__(self, t: float = 0.0) -> None:
        self.t = t

    def now(self) -> float:
        return self.t

    def set(self, t: float) -> None:
        self.t = t

    def advance(self, dt: float) -> None:
        self.t += dt

    def pause(self) -> None:  # pragma: no cover - parity with SessionClock
        pass

    def resume(self) -> None:  # pragma: no cover - parity with SessionClock
        pass

    @property
    def paused(self) -> bool:  # pragma: no cover - parity with SessionClock
        return False


def fmt_hms(seconds: float | None) -> str:
    """Format session seconds as HH:MM:SS. Negative values clamp to zero."""
    if seconds is None:
        return "--:--:--"
    s = max(0, int(round(seconds)))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{sec:02d}"


def fmt_srt_time(seconds: float) -> str:
    """Format session seconds as an SRT timestamp (HH:MM:SS,mmm)."""
    total_ms = max(0, int(round(seconds * 1000)))
    ms = total_ms % 1000
    total_s = total_ms // 1000
    h, rem = divmod(total_s, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def atomic_write_text(path: Path | str, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def atomic_write_json(path: Path | str, obj: Any) -> None:
    atomic_write_text(path, json.dumps(obj, ensure_ascii=False, indent=2, default=str))
