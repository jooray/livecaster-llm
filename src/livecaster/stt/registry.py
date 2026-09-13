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
from livecaster.stt.postprocess import wrong_script

log = get_logger(__name__)

MAC_ARM = platform.system() == "Darwin" and platform.machine() == "arm64"

#: Engine key -> the package that has to be installed for it, for the Settings
#: dialog. `auto` needs whatever the platform default turns out to be.
ENGINE_PACKAGES: dict[str, str] = {
    "parakeet-mlx": "parakeet_mlx",
    "whisper-mlx": "mlx_whisper",
    "onnx-asr": "onnx_asr",
    "faster-whisper": "faster_whisper",
}


def _build(engine_key: str, model: str, cpu_threads: int = 0) -> STTEngine:
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

        return FasterWhisperEngine(model, cpu_threads=cpu_threads)
    if engine_key == "mock":
        from livecaster.stt.mock import MockEngine

        return MockEngine()
    raise ValueError(f"unknown STT engine {engine_key!r}")


def engine_catalogue() -> list[dict[str, Any]]:
    """Every selectable engine, with whether its extra is actually installed.

    Only looks for the module, so nothing is imported and no weights are touched:
    the dialog can say "not installed" instead of the host finding out at Start.
    """
    import importlib.util

    out: list[dict[str, Any]] = [
        {
            "key": "auto",
            "installed": True,
            "can_force_language": None,
            "note": f"this machine: {default_engine_key()}",
        }
    ]
    for key, package in ENGINE_PACKAGES.items():
        installed = importlib.util.find_spec(package) is not None
        out.append(
            {
                "key": key,
                "installed": installed,
                "can_force_language": key in {"whisper-mlx", "faster-whisper"},
                "note": "" if installed else f"needs {package}",
            }
        )
    return out


def default_engine_key() -> str:
    return "parakeet-mlx" if MAC_ARM else "onnx-asr"


def fallback_engine_key() -> str:
    return "whisper-mlx" if MAC_ARM else "faster-whisper"


def select_engine(
    engine: str = "auto",
    model: str = "",
    language: str = "auto",
    cpu_threads: int = 0,
) -> STTEngine:
    """`auto` picks the platform default, then falls back when the language is out of set."""
    lang = (language or "auto").lower()
    if engine and engine != "auto":
        built = _build(engine, model, cpu_threads)
    else:
        key = default_engine_key()
        built = _build(key, model, cpu_threads)
        if lang != "auto" and built.languages is not None and lang not in built.languages:
            alt = fallback_engine_key()
            log.info("%s does not cover %r, falling back to %s", key, lang, alt)
            built = _build(alt, model, cpu_threads)
    if lang != "auto" and not built.can_force_language:
        log.warning(
            "%s detects the language itself: stt.language=%r is a hint for the notes, not a lock. "
            "Use stt.engine=faster-whisper for a real one.",
            built.name,
            lang,
        )
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
        self.dropped_language = 0
        self.errors = 0
        self.restarts = 0
        self.last_error: str | None = None

    @property
    def depth(self) -> int:
        return self.queue.qsize()

    @property
    def forced(self) -> bool:
        """A locked language the engine can actually honour."""
        return bool(self.language) and self.engine.can_force_language

    def set_language(self, language: str | None) -> None:
        """Change the language mid-session. Read per utterance, so no restart."""
        self.language = None if not language or language == "auto" else language.lower()

    def status(self) -> dict[str, Any]:
        return {
            "engine": self.engine.name,
            "queue": self.depth,
            "last_latency_ms": self.last_latency_ms,
            "processed": self.processed,
            "language": self.language or "auto",
            "can_force_language": self.engine.can_force_language,
            "dropped_language": self.dropped_language,
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
            # An engine that detects the language for itself sometimes answers in
            # the wrong alphabet entirely. That is never a real transcript.
            if self.language and wrong_script(result.text, self.language):
                self.dropped_language += 1
                log.info("dropped a %s utterance: %r", self.language, result.text[:60])
                continue
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
