"""Engine selection (SPEC §7.3) plus the single STT worker thread."""

from __future__ import annotations

import platform
import queue
import threading
import time
from collections.abc import Callable
from typing import Any

from livecaster.audio.segmenter import Utterance
from livecaster.log import get_logger
from livecaster.stt.base import EngineUnavailable, STTEngine, STTResult

log = get_logger(__name__)

MAC_ARM = platform.system() == "Darwin" and platform.machine() == "arm64"


def _build(engine_key: str, model: str) -> STTEngine:
    if engine_key == "parakeet-mlx":
        from livecaster.stt.parakeet_mlx import ParakeetMLXEngine

        return ParakeetMLXEngine(model)
    if engine_key == "whisper-mlx":
        from livecaster.stt.whisper_mlx import WhisperMLXEngine

        return WhisperMLXEngine(model)
    if engine_key == "onnx-asr":
        from livecaster.stt.onnx_asr import OnnxASREngine

        return OnnxASREngine(model)
    if engine_key == "faster-whisper":
        from livecaster.stt.faster_whisper import FasterWhisperEngine

        return FasterWhisperEngine(model)
    if engine_key == "mock":
        from livecaster.stt.mock import MockEngine

        return MockEngine()
    raise ValueError(f"unknown STT engine {engine_key!r}")


def default_engine_key() -> str:
    return "parakeet-mlx" if MAC_ARM else "onnx-asr"


def fallback_engine_key() -> str:
    return "whisper-mlx" if MAC_ARM else "faster-whisper"


def select_engine(engine: str = "auto", model: str = "", language: str = "auto") -> STTEngine:
    """`auto` picks the platform default, then falls back when the language is out of set."""
    if engine and engine != "auto":
        return _build(engine, model)
    key = default_engine_key()
    built = _build(key, model)
    lang = (language or "auto").lower()
    if lang != "auto" and built.languages is not None and lang not in built.languages:
        alt = fallback_engine_key()
        log.info("%s does not cover %r, falling back to %s", key, lang, alt)
        return _build(alt, model)
    return built


class STTWorker:
    """One thread owns the model: MLX and ONNX sessions are not thread-safe."""

    def __init__(
        self,
        engine: STTEngine,
        on_result: Callable[[Utterance, STTResult], None],
        *,
        language: str = "auto",
        max_queue: int = 256,
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        self.engine = engine
        self.on_result = on_result
        self.on_error = on_error
        self.language = None if language == "auto" else language
        self.queue: queue.Queue[Utterance | None] = queue.Queue(maxsize=max_queue)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.last_latency_ms: int | None = None
        self.processed = 0
        self.errors = 0
        self.restarts = 0
        self.last_error: str | None = None

    @property
    def depth(self) -> int:
        return self.queue.qsize()

    def status(self) -> dict[str, Any]:
        return {
            "engine": self.engine.name,
            "queue": self.depth,
            "last_latency_ms": self.last_latency_ms,
            "processed": self.processed,
            "errors": self.errors,
            "restarts": self.restarts,
            "error": self.last_error,
        }

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="stt-worker", daemon=True)
        self._thread.start()

    def submit(self, utterance: Utterance) -> bool:
        try:
            self.queue.put_nowait(utterance)
            return True
        except queue.Full:
            log.warning("STT queue full, dropping %.1f s from %s", utterance.duration, utterance.channel)
            return False

    def stop(self, drain: bool = True) -> None:
        if self._thread is None:
            return
        if drain:
            self.queue.put(None)
            self._thread.join(timeout=120)
        else:
            self._stop.set()
            self._thread.join(timeout=5)
        self._thread = None

    def _run(self) -> None:
        try:
            self.engine.warmup()
        except Exception as exc:
            self.last_error = str(exc)
            log.error("STT warmup failed: %s", exc)
            if self.on_error:
                self.on_error(exc)
            return
        while not self._stop.is_set():
            try:
                item = self.queue.get(timeout=0.2)
            except queue.Empty:
                continue
            if item is None:
                break
            started = time.monotonic()
            try:
                result = self.engine.transcribe(item.audio, self.language)
            except EngineUnavailable as exc:
                self.errors += 1
                self.last_error = str(exc)
                log.warning("STT engine error, restarting once: %s", exc)
                if not self._restart():
                    if self.on_error:
                        self.on_error(exc)
                    break
                try:
                    result = self.engine.transcribe(item.audio, self.language)
                except Exception as exc2:
                    self.errors += 1
                    self.last_error = str(exc2)
                    continue
            except Exception as exc:  # pragma: no cover - defensive
                self.errors += 1
                self.last_error = str(exc)
                log.exception("STT failed on a %.1f s utterance", item.duration)
                continue
            self.last_latency_ms = int((time.monotonic() - started) * 1000)
            self.processed += 1
            if result.text.strip():
                try:
                    self.on_result(item, result)
                except Exception:  # pragma: no cover
                    log.exception("STT result handler failed")

    def _restart(self) -> bool:
        if self.restarts >= 1:
            return False
        self.restarts += 1
        try:
            self.engine.warmup()
            return True
        except Exception as exc:
            self.last_error = str(exc)
            log.error("STT restart failed: %s", exc)
            return False
