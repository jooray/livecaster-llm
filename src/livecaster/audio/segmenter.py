"""VAD segmenter: frames in, utterances out (SPEC §7.3, PLAN §5.2)."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

import numpy as np

FRAME_SAMPLES = 512
FRAME_MS = 32.0
SAMPLE_RATE = 16_000


@dataclass
class Utterance:
    channel: str
    t0: float
    t1: float
    audio: np.ndarray = field(repr=False)

    @property
    def duration(self) -> float:
        return self.t1 - self.t0


class Segmenter:
    """`idle` → `speech` → emit after enough silence, with a hard cut at ``max_utterance_s``."""

    def __init__(
        self,
        channel: str,
        vad,  # noqa: ANN001 - a callable returning speech probability
        *,
        threshold: float = 0.5,
        silence_ms: int = 600,
        min_speech_ms: int = 300,
        max_utterance_s: float = 20.0,
        preroll_ms: int = 300,
    ) -> None:
        self.channel = channel
        self.vad = vad
        self.threshold = threshold
        self.silence_ms = silence_ms
        self.min_speech_ms = min_speech_ms
        self.max_utterance_s = max_utterance_s
        self.preroll = deque(maxlen=max(1, int(preroll_ms / FRAME_MS)))
        self.state = "idle"
        self.buf: list[tuple[np.ndarray, float]] = []
        self.t0 = 0.0
        self.silence = 0.0
        self.speech_ms = 0.0
        self.tail: deque[tuple[float, int]] = deque(maxlen=int(3000 / FRAME_MS))

    def push(self, frame: np.ndarray, t: float) -> list[Utterance]:
        p = float(self.vad(frame))
        speech = p >= self.threshold
        if self.state == "idle":
            self.preroll.append((frame, t))
            if speech:
                self.state = "speech"
                self.buf = list(self.preroll)
                self.t0 = self.buf[0][1]
                self.silence = 0.0
                self.speech_ms = FRAME_MS
                self.tail.clear()
                self.tail.append((p, len(self.buf) - 1))
            return []

        self.buf.append((frame, t))
        self.tail.append((p, len(self.buf) - 1))
        if speech:
            self.silence = 0.0
            self.speech_ms += FRAME_MS
        else:
            self.silence += FRAME_MS

        if self.silence >= self.silence_ms:
            return self._emit()
        if (t - self.t0) >= self.max_utterance_s:
            return self._emit(cut_at=self._quietest_index())
        return []

    def _quietest_index(self) -> int:
        if not self.tail:
            return len(self.buf) - 1
        return min(self.tail, key=lambda pi: pi[0])[1]

    def _emit(self, cut_at: int | None = None) -> list[Utterance]:
        if not self.buf:
            self._reset()
            return []
        end = len(self.buf) if cut_at is None else max(1, min(cut_at + 1, len(self.buf)))
        head = self.buf[:end]
        tail = self.buf[end:]
        out: list[Utterance] = []
        if self.speech_ms >= self.min_speech_ms and head:
            audio = np.concatenate([f for f, _ in head])
            t0 = head[0][1]
            t1 = head[-1][1] + FRAME_MS / 1000.0
            out.append(Utterance(channel=self.channel, t0=t0, t1=t1, audio=audio))
        if cut_at is None or not tail:
            self._reset()
        else:
            self.buf = tail
            self.t0 = tail[0][1]
            self.silence = 0.0
            self.speech_ms = FRAME_MS * len(tail)
            self.tail.clear()
            self.state = "speech"
        return out

    def _reset(self) -> None:
        self.state = "idle"
        self.buf = []
        self.silence = 0.0
        self.speech_ms = 0.0
        self.tail.clear()
        self.preroll.clear()

    def flush(self) -> list[Utterance]:
        """Emit whatever is buffered; called when capture stops."""
        if self.state != "speech":
            return []
        return self._emit()
