"""The orchestrator: audio -> VAD -> STT -> transcript -> reasoner -> broadcast."""

from __future__ import annotations

import asyncio
import math
import queue
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

from livecaster.audio.recorder import WavRecorder
from livecaster.audio.segmenter import Segmenter, Utterance
from livecaster.audio.sources import make_source
from livecaster.audio.vad import make_vad
from livecaster.config import ChannelConfig, Config
from livecaster.llm.reasoner import Reasoner
from livecaster.llm.wrapup import run_wrapup
from livecaster.log import get_logger
from livecaster.outline.parser import parse_outline_file
from livecaster.outline.remap import reindex_new_nodes, remap
from livecaster.session.fastlane import FastLane
from livecaster.session.models import Patch, Segment
from livecaster.session.reducer import ManualAction, apply_manual, apply_warm, decay_warm
from livecaster.session.store import SessionStore
from livecaster.session.transcript import Transcript
from livecaster.stt.base import STTResult
from livecaster.stt.registry import STTWorker, select_engine
from livecaster.timeutil import SessionClock

log = get_logger(__name__)

LEVEL_WINDOW_S = 0.1
MAX_BUFFER_S = 60.0


class ChannelPipeline:
    """One channel: source -> frame queue -> VAD/segmenter thread -> STT queue."""

    def __init__(
        self,
        cfg: ChannelConfig,
        config: Config,
        clock: Callable[[], float],
        on_utterance: Callable[[Utterance], None],
        recorder: WavRecorder | None = None,
        *,
        speed: float = 1.0,
        on_finished: Callable[[], None] | None = None,
    ) -> None:
        self.cfg = cfg
        self.config = config
        self.clock = clock
        self.on_utterance = on_utterance
        self.recorder = recorder
        self.frames: queue.Queue[tuple[np.ndarray, float] | None] = queue.Queue(
            maxsize=int(MAX_BUFFER_S * 16_000 / 512)
        )
        self.level_db = -90.0
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._paused = threading.Event()
        self.dropped_frames = 0
        self.segmenter = Segmenter(
            cfg.name,
            make_vad(),
            threshold=config.stt.vad_threshold,
            silence_ms=config.stt.silence_ms,
            min_speech_ms=config.stt.min_speech_ms,
            max_utterance_s=config.stt.max_utterance_s,
            preroll_ms=config.stt.preroll_ms,
        )
        self.source = make_source(
            cfg.name,
            cfg.source,
            channel_index=cfg.channel_index,
            clock=clock,
            speed=speed,
            on_finished=on_finished,
        )

    # --- capture -----------------------------------------------------------

    def _on_frames(self, frame: np.ndarray, t: float) -> None:
        """Runs on the PortAudio / file thread: copy and get out of the way."""
        try:
            self.frames.put_nowait((frame.copy(), t))
        except queue.Full:
            self.dropped_frames += 1

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name=f"seg-{self.cfg.name}", daemon=True)
        self._thread.start()
        self.source.start(self._on_frames)

    def pause(self) -> None:
        self._paused.set()

    def resume(self) -> None:
        self._paused.clear()

    def stop(self) -> None:
        try:
            self.source.stop()
        except Exception:  # pragma: no cover
            log.debug("%s: source stop failed", self.cfg.name, exc_info=True)
        self.frames.put(None)
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        if self.recorder is not None:
            self.recorder.close()

    def _run(self) -> None:
        acc = np.zeros(0, dtype=np.float32)
        while not self._stop.is_set():
            try:
                item = self.frames.get(timeout=0.2)
            except queue.Empty:
                continue
            if item is None:
                break
            frame, t = item
            if self._paused.is_set():
                continue
            if self.recorder is not None:
                self.recorder.write(frame)
            acc = np.concatenate([acc, frame])
            if acc.size >= LEVEL_WINDOW_S * 16_000:
                rms = float(np.sqrt(np.mean(np.square(acc))))
                self.level_db = 20.0 * math.log10(max(rms, 1e-9))
                acc = np.zeros(0, dtype=np.float32)
            for utt in self.segmenter.push(frame, t):
                self.on_utterance(utt)
        for utt in self.segmenter.flush():
            self.on_utterance(utt)


class Engine:
    """Owns the pipeline for one session."""

    def __init__(
        self,
        store: SessionStore,
        config: Config,
        client: Any,
        *,
        clock: Any | None = None,
        replay_speed: float = 1.0,
        on_replay_finished: Callable[[], None] | None = None,
        autostart: bool = False,
    ) -> None:
        self.store = store
        self.config = config
        self.client = client
        self.clock = clock or SessionClock()
        self.replay_speed = replay_speed
        self.on_replay_finished = on_replay_finished
        self.autostart = autostart
        self.transcript = Transcript(direct_channels=[c.name for c in store.session.channels if c.is_direct])
        self.fastlane = FastLane(store.outline, store.session.preflight)
        self.reasoner = Reasoner(store, self.transcript, client, config, self.clock)
        self.channels: list[ChannelPipeline] = []
        self.stt: STTWorker | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self._tasks: list[asyncio.Task[Any]] = []
        self._finished = asyncio.Event()
        self._finish_lock = asyncio.Lock()
        self.finish_result: dict[str, str] | None = None
        self.finish_error: str | None = None
        self._sources_finished = 0
        self._outline_reloads = 0

    # --- lifecycle ---------------------------------------------------------

    @property
    def show_speakers(self) -> bool:
        return len(self.store.session.channels) > 1

    async def startup(self) -> None:
        self.loop = asyncio.get_running_loop()
        self._tasks.append(asyncio.create_task(self.store.snapshot_loop(), name="snapshot"))
        self._tasks.append(asyncio.create_task(self._warm_loop(), name="warm-decay"))
        self.reasoner.start()
        if self.autostart:
            await self.start_capture()

    def _build_stt(self) -> STTWorker:
        engine = select_engine(self.config.stt.engine, self.config.stt.model, self.config.stt.language)
        return STTWorker(
            engine,
            self._on_stt_result,
            language=self.config.stt.language,
            on_error=self._on_stt_error,
        )

    async def start_capture(self) -> None:
        session = self.store.session
        if session.status == "running":
            return
        if session.status == "paused":
            await self.resume()
            return
        self.stt = self._build_stt()
        self.stt.start()
        audio_dir = self.store.dir / "audio"
        for cfg in session.channels:
            recorder = None
            if self.config.audio.record and cfg.record and not cfg.source.startswith("file:"):
                recorder = WavRecorder(WavRecorder.unique_path(audio_dir, cfg.name.lower()))
                recorder.open()
            pipeline = ChannelPipeline(
                cfg,
                self.config,
                self.clock.now,
                self._on_utterance,
                recorder,
                speed=self.replay_speed,
                on_finished=self._on_source_finished,
            )
            self.channels.append(pipeline)
        try:
            for pipeline in self.channels:
                pipeline.start()
        except Exception as exc:
            log.error("cannot start capture: %s", exc)
            for pipeline in self.channels:
                try:
                    pipeline.stop()
                except Exception:  # pragma: no cover - already failing
                    pass
            self.channels.clear()
            if self.stt is not None:
                self.stt.stop(drain=False)
                self.stt = None
            self.store.log_event("start_failed", self.clock.now(), error=str(exc)[:300])
            self.store.emit("toast", {"level": "error", "text": f"Cannot start capture: {exc}"})
            raise
        session.status = "running"
        self.store.mark_dirty()
        self.store.log_event("start", self.clock.now())
        self.store.emit("status_changed", "running")
        log.info("capture started on %d channel(s)", len(self.channels))

    async def pause(self) -> None:
        session = self.store.session
        if session.status != "running":
            return
        for pipeline in self.channels:
            pipeline.pause()
        self.clock.pause()
        session.status = "paused"
        self.store.mark_dirty()
        self.store.snapshot(force=True)
        self.store.log_event("pause", self.clock.now())
        self.store.emit("status_changed", "paused")

    async def resume(self) -> None:
        session = self.store.session
        if session.status != "paused":
            return
        self.clock.resume()
        for pipeline in self.channels:
            pipeline.resume()
        session.status = "running"
        self.store.mark_dirty()
        self.store.log_event("resume", self.clock.now())
        self.store.emit("status_changed", "running")

    async def finish(self) -> dict[str, str]:
        async with self._finish_lock:
            session = self.store.session
            if session.status == "finished" and self.finish_result is not None:
                return self.finish_result
            session.status = "finishing"
            self.store.emit("status_changed", "finishing")
            self.store.emit("toast", {"level": "info", "text": "Finishing: flushing audio and transcribing…"})
            now = self.clock.now()
            session.duration_s = max(session.duration_s, now, self.transcript.duration())
            for pipeline in self.channels:
                await asyncio.to_thread(pipeline.stop)
            if self.stt is not None:
                await asyncio.to_thread(self.stt.stop, True)
            await self.reasoner.stop()
            if self.transcript.segments:
                self.store.emit("toast", {"level": "info", "text": "Running the final tick…"})
                try:
                    await self.reasoner.tick(now)
                except Exception as exc:  # pragma: no cover - already logged
                    log.warning("final tick failed: %s", exc)
            self.store.emit("toast", {"level": "info", "text": "Writing show notes…"})
            try:
                paths = await run_wrapup(self.store, self.transcript, self.client, self.config)
                self.finish_result = paths
            except Exception as exc:
                self.finish_error = f"{type(exc).__name__}: {exc}"
                log.error("wrap-up failed: %s", self.finish_error)
                paths = {}
            session.status = "finished"
            self.store.mark_dirty()
            self.store.snapshot(force=True)
            self.store.log_event("finish", now, paths=paths, error=self.finish_error)
            self.store.emit(
                "done",
                {
                    "paths": paths,
                    "duration_s": session.duration_s,
                    "cost_usd": session.usage.cost_usd,
                    "ticks": session.usage.ticks,
                    "error": self.finish_error,
                },
            )
            self._finished.set()
            return paths

    async def shutdown(self) -> None:
        for pipeline in self.channels:
            await asyncio.to_thread(pipeline.stop)
        if self.stt is not None:
            await asyncio.to_thread(self.stt.stop, False)
        await self.reasoner.stop()
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: B014
                pass
        self._tasks.clear()
        self.store.close()
        try:
            await self.client.aclose()
        except Exception:  # pragma: no cover
            pass

    async def wait_finished(self) -> None:
        await self._finished.wait()

    # --- audio -> transcript ----------------------------------------------

    def _on_source_finished(self) -> None:
        self._sources_finished += 1
        if self.loop and self._sources_finished >= max(1, len(self.channels)):
            if self.on_replay_finished is not None:
                self.loop.call_soon_threadsafe(self.on_replay_finished)

    def _on_utterance(self, utterance: Utterance) -> None:
        if self.stt is not None:
            self.stt.submit(utterance)

    def _on_stt_error(self, exc: Exception) -> None:
        if self.loop:
            self.loop.call_soon_threadsafe(
                self.store.emit, "toast", {"level": "error", "text": f"STT stopped: {exc}"}
            )

    def _on_stt_result(self, utterance: Utterance, result: STTResult) -> None:
        """Runs on the STT thread; hop to the asyncio loop before touching state."""
        if self.loop is None:
            return
        self.loop.call_soon_threadsafe(self._ingest, utterance, result)

    def _ingest(self, utterance: Utterance, result: STTResult) -> None:
        segment = Segment(
            id=self.transcript.next_id(),
            channel=utterance.channel,
            speaker=utterance.channel if self.show_speakers else None,
            t0=utterance.t0,
            t1=utterance.t1,
            text=result.text,
            language=result.language,
            words=result.words,
            engine=self.stt.engine.name if self.stt else "",
        )
        self.add_segment(segment)

    def add_segment(self, segment: Segment) -> Segment | None:
        kept = self.transcript.append(segment)
        if kept is None:
            return None
        self.store.append_segment(kept)
        self.store.emit("segment", kept)
        scores = self.fastlane.score_segment(kept)
        if scores:
            patch = apply_warm(self.store.session, scores, self.clock.now())
            self.store.apply_patch(patch)
        return kept

    async def _warm_loop(self) -> None:
        """Housekeeping: decay the fast lane and keep `duration_s` honest for a resume."""
        try:
            while True:
                await asyncio.sleep(10.0)
                now = self.clock.now()
                if self.store.session.status == "running":
                    self.store.session.duration_s = max(self.store.session.duration_s, now)
                    self.store.mark_dirty()
                patch = decay_warm(self.store.session, now)
                if not patch.is_empty():
                    self.store.apply_patch(patch)
        except asyncio.CancelledError:  # pragma: no cover
            raise

    # --- controls ----------------------------------------------------------

    def manual(self, action: ManualAction) -> Patch:
        patch = apply_manual(self.store.session, action, self.clock.now(), self.config.llm)
        self.store.log_event(
            "manual", self.clock.now(), action=action.kind, node=action.node_id, status=action.status
        )
        self.store.apply_patch(patch)
        return patch

    def sync_mark(self) -> float:
        t = self.clock.now()
        self.store.session.sync_marks.append(t)
        self.store.mark_dirty()
        self.store.log_event("sync_mark", t)
        self.store.emit("toast", {"level": "success", "text": f"Sync mark at {t:.1f}s"})
        return t

    def request_tick(self) -> None:
        self.reasoner.request_tick()

    def reload_outline(self) -> bool:
        """Re-parse the source outline, carry states across, rebuild derived data (FR-04)."""
        session = self.store.session
        path = Path(session.outline_path)
        if not path.is_file():
            self.store.emit("toast", {"level": "error", "text": f"outline not found: {path}"})
            return False
        try:
            new_outline = parse_outline_file(path)
        except Exception as exc:  # pragma: no cover - the parser does not raise
            self.store.emit("toast", {"level": "error", "text": f"outline parse failed: {exc}"})
            return False
        old_outline = self.store.outline
        states, retired, id_map = remap(old_outline, new_outline, dict(session.nodes))
        next_id = reindex_new_nodes(new_outline, id_map, max(session.outline_next_id, old_outline.next_id))
        # After reindexing, matched nodes carry their old IDs again, so the surviving
        # states are simply everything that was not retired.
        session.nodes = {k: v for k, v in session.nodes.items() if k not in retired}
        _ = states
        session.retired_nodes.update(retired)
        session.outline = new_outline.nodes
        session.outline_next_id = next_id
        self.store.outline = new_outline
        self._outline_reloads += 1
        self.store.save_outline_copy(path, self._outline_reloads)
        self.fastlane.rebuild(new_outline, session.preflight)
        self.reasoner.invalidate_prompt()
        for ref in list(session.suggestions.next):
            if ref.node_id not in new_outline:
                session.suggestions.next.remove(ref)
        self.store.mark_dirty()
        self.store.snapshot(force=True)
        self.store.log_event("reload_outline", self.clock.now(), nodes=len(new_outline.nodes))
        self.store.emit("state", session)
        self.store.emit(
            "toast", {"level": "success", "text": f"Outline reloaded ({len(new_outline.leaves())} items)"}
        )
        return True

    # --- status ------------------------------------------------------------

    def status_payload(self) -> dict[str, Any]:
        return {
            "audio": {p.cfg.name: round(p.level_db, 1) for p in self.channels},
            "stt": self.stt.status() if self.stt else {"engine": self.config.stt.engine, "queue": 0},
            "llm": self.reasoner.status.as_dict(),
            "clock": round(self.clock.now(), 2),
            "session_status": self.store.session.status,
            "sync_marks": list(self.store.session.sync_marks),
        }


def watch_outline(engine: Engine, stop: threading.Event) -> threading.Thread:
    """Background watcher that calls :meth:`Engine.reload_outline` on file changes."""

    def run() -> None:
        from watchfiles import watch

        path = Path(engine.store.session.outline_path)
        if not path.is_file():
            return
        for _changes in watch(str(path), stop_event=stop, debounce=400, step=200):
            loop = engine.loop
            if loop is None:
                continue
            loop.call_soon_threadsafe(engine.reload_outline)

    thread = threading.Thread(target=run, name="outline-watch", daemon=True)
    thread.start()
    return thread


def now_ms() -> int:
    return int(time.time() * 1000)
