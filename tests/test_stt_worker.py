"""The STT worker thread: live language changes and the language guard (D20)."""

from __future__ import annotations

import time

import numpy as np

from livecaster.audio.segmenter import Utterance
from livecaster.stt.base import STTResult
from livecaster.stt.registry import STTWorker


class ScriptedEngine:
    """Returns whatever it is told to, and records the language it was given."""

    name = "scripted"
    languages = None

    def __init__(self, texts: list[str], can_force_language: bool = False) -> None:
        self.texts = list(texts)
        self.can_force_language = can_force_language
        self.seen_languages: list[str | None] = []
        self._i = 0

    def warmup(self) -> None:
        pass

    def transcribe(self, audio: np.ndarray, language: str | None) -> STTResult:
        self.seen_languages.append(language)
        text = self.texts[min(self._i, len(self.texts) - 1)]
        self._i += 1
        return STTResult(text=text, language=language)


def utterance(name: str = "Room") -> Utterance:
    return Utterance(channel=name, t0=0.0, t1=1.0, audio=np.zeros(16_000, dtype=np.float32))


def run(worker: STTWorker, count: int, results: list) -> None:
    worker.start()
    for _ in range(count):
        worker.submit(utterance())
    deadline = time.monotonic() + 5
    while worker.processed < count and time.monotonic() < deadline:
        time.sleep(0.01)
    worker.stop(drain=True)


def test_a_locked_language_reaches_the_engine():
    engine = ScriptedEngine(["ahoj"], can_force_language=True)
    got: list = []
    worker = STTWorker(engine, lambda u, r: got.append(r.text), language="sk")
    run(worker, 2, got)
    assert engine.seen_languages == ["sk", "sk"]
    assert worker.forced is True


def test_auto_means_the_engine_decides():
    engine = ScriptedEngine(["ahoj"], can_force_language=True)
    got: list = []
    worker = STTWorker(engine, lambda u, r: got.append(r.text), language="auto")
    run(worker, 1, got)
    assert engine.seen_languages == [None]
    assert worker.forced is False


def test_the_language_can_change_mid_session():
    engine = ScriptedEngine(["ahoj"], can_force_language=True)
    got: list = []
    worker = STTWorker(engine, lambda u, r: got.append(r.text), language="auto")
    worker.start()
    worker.submit(utterance())
    while worker.processed < 1:
        time.sleep(0.01)
    worker.set_language("sk")
    worker.submit(utterance())
    while worker.processed < 2:
        time.sleep(0.01)
    worker.set_language("auto")
    worker.submit(utterance())
    while worker.processed < 3:
        time.sleep(0.01)
    worker.stop(drain=True)
    assert engine.seen_languages == [None, "sk", None]


def test_wrong_alphabet_utterances_are_dropped_under_a_lock():
    # Exactly what Parakeet returned for Slovak speech on 2026-09-03.
    engine = ScriptedEngine(["Ну, мой брат за мною."])
    got: list = []
    worker = STTWorker(engine, lambda u, r: got.append(r.text), language="sk")
    run(worker, 1, got)
    assert got == []
    assert worker.dropped_language == 1
    assert worker.status()["can_force_language"] is False


def test_nothing_is_dropped_without_a_lock():
    engine = ScriptedEngine(["Ну, мой брат за мною."])
    got: list = []
    worker = STTWorker(engine, lambda u, r: got.append(r.text), language="auto")
    run(worker, 1, got)
    assert got == ["Ну, мой брат за мною."]
    assert worker.dropped_language == 0
