"""VAD segmentation on synthetic audio (M4)."""

from __future__ import annotations

import numpy as np

from livecaster.audio.segmenter import FRAME_SAMPLES, Segmenter
from livecaster.audio.vad import EnergyVAD

RATE = 16_000
FRAME_S = FRAME_SAMPLES / RATE


def tone(seconds: float, freq: float = 220.0, amp: float = 0.4) -> np.ndarray:
    n = int(seconds * RATE)
    t = np.arange(n) / RATE
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def silence(seconds: float) -> np.ndarray:
    return np.zeros(int(seconds * RATE), dtype=np.float32)


def feed(segmenter: Segmenter, audio: np.ndarray, t0: float = 0.0):
    out = []
    for i in range(0, len(audio) - FRAME_SAMPLES + 1, FRAME_SAMPLES):
        frame = audio[i : i + FRAME_SAMPLES]
        out.extend(segmenter.push(frame, t0 + i / RATE))
    return out


def make(**kw) -> Segmenter:
    kw.setdefault("threshold", 0.5)
    return Segmenter("Host", EnergyVAD(threshold=0.05), **kw)


def test_two_bursts_become_two_utterances():
    seg = make()
    audio = np.concatenate([silence(0.5), tone(1.2), silence(1.0), tone(1.0), silence(1.0)])
    utterances = feed(seg, audio)
    assert len(utterances) == 2
    assert abs(utterances[0].t0 - 0.5) < 0.35  # pre-roll pulls the start earlier
    assert abs(utterances[0].t1 - 1.7) < 0.7
    assert abs(utterances[1].t0 - 2.7) < 0.35


def test_short_blip_is_dropped():
    seg = make(min_speech_ms=300)
    utterances = feed(seg, np.concatenate([silence(0.3), tone(0.1), silence(1.2)]))
    assert utterances == []


def test_silence_shorter_than_the_gap_does_not_split():
    seg = make(silence_ms=600)
    audio = np.concatenate([tone(0.8), silence(0.3), tone(0.8), silence(1.0)])
    utterances = feed(seg, audio)
    assert len(utterances) == 1
    assert utterances[0].duration > 1.7


def test_hard_cut_at_the_maximum_length():
    seg = make(max_utterance_s=2.0, silence_ms=600)
    utterances = feed(seg, np.concatenate([tone(6.0), silence(1.0)]))
    assert len(utterances) >= 3
    assert all(u.duration <= 2.5 for u in utterances)
    assert sum(u.duration for u in utterances) > 5.0


def test_preroll_is_included():
    seg = make(preroll_ms=300)
    utterances = feed(seg, np.concatenate([silence(1.0), tone(1.0), silence(1.0)]))
    assert utterances
    assert utterances[0].t0 < 1.0
    assert utterances[0].t0 > 1.0 - 0.35


def test_flush_emits_speech_in_progress():
    seg = make()
    assert feed(seg, np.concatenate([silence(0.3), tone(1.0)])) == []
    flushed = seg.flush()
    assert len(flushed) == 1
    assert flushed[0].duration > 0.9


def test_audio_is_contiguous_and_float32():
    seg = make()
    utterances = feed(seg, np.concatenate([silence(0.3), tone(1.0), silence(1.0)]))
    audio = utterances[0].audio
    assert audio.dtype == np.float32
    assert len(audio) % FRAME_SAMPLES == 0
