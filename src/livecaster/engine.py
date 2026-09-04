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
from livecaster.llm.preflight import run_preflight
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
        native_recorder: WavRecorder | None = None,
    ) -> None:
        self.cfg = cfg
        self.config = config
        self.clock = clock
        self.on_utterance = on_utterance
        self.recorder = recorder
        self.native_recorder = native_recorder
        self.frames: queue.Queue[tuple[np.ndarray, float] | None] = queue.Queue(
            maxsize=int(MAX_BUFFER_S * 16_000 / 512)
        )
        self.native_frames: queue.Queue[tuple[np.ndarray, int]] = queue.Queue(maxsize=2000)
        self.dropped_native = 0
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
            on_native=self._on_native if native_recorder is not None else None,
        )

    def _on_native(self, block: np.ndarray, rate: int) -> None:
        """Runs on the PortAudio thread, so only enqueue — the channel thread writes."""
        try:
            self.native_frames.put_nowait((block, rate))
        except queue.Full:
            self.dropped_native += 1

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
        if self.native_recorder is not None:
            self.native_recorder.close()

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
            self._drain_native()
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
        self._drain_native()
        for utt in self.segmenter.flush():
            self.on_utterance(utt)

    def _drain_native(self) -> None:
        recorder = self.native_recorder
        if recorder is None:
            return
        while True:
            try:
                block, rate = self.native_frames.get_nowait()
            except queue.Empty:
                return
            if self._paused.is_set():
                continue
            recorder.ensure_open(rate)
            recorder.write(block)


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
        # Session time is recording time: it stands still until the first Start and
        # between Finish and Record again, so the SRT lines up with the WAV backup.
        self.clock = clock or SessionClock(paused=True)
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
        self._finish_task: asyncio.Task[dict[str, str]] | None = None
        #: (engine name, can_force_language) probed once, so the UI's language pill
        #: is honest before Start as well as after it.
        self._stt_facts: tuple[str, bool | None] | None = None
        #: Set by the CLI when the pre-flight pass should run once the loop is up,
        #: so the UI opens at once instead of waiting on the model.
        self.preflight_pending = False

    # --- lifecycle ---------------------------------------------------------

    @property
    def show_speakers(self) -> bool:
        return len(self.store.session.channels) > 1

    async def startup(self) -> None:
        self.loop = asyncio.get_running_loop()
        self._tasks.append(asyncio.create_task(self.store.snapshot_loop(), name="snapshot"))
        self._tasks.append(asyncio.create_task(self._warm_loop(), name="warm-decay"))
        self.reasoner.start()
        if self.preflight_pending:
            self.preflight_pending = False
            self._tasks.append(asyncio.create_task(self._preflight_in_background(), name="preflight"))
        if self.autostart:
            await self.start_capture()

    async def _preflight_in_background(self) -> None:
        """One LLM call for questions and trigger phrases, while the host already sees the map."""
        self.store.emit(
            "toast", {"level": "info", "text": "Pre-flight: asking the model for questions and triggers…"}
        )
        try:
            pf = await run_preflight(self.store, self.client, self.config)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - run_preflight already swallows failures
            log.warning("pre-flight crashed: %s", exc)
            pf = None
        if pf is None:
            self.store.emit(
                "toast", {"level": "warn", "text": "Pre-flight failed; the map works without it."}
            )
            return
        self.fastlane.rebuild(self.store.outline, pf)
        # The pass may have settled the language, which is baked into the tick prompt.
        self.reasoner.invalidate_prompt()
        self.store.mark_dirty()
        self.store.snapshot(force=True)
        self.store.emit("state", self.store.session)
        self.store.emit(
            "toast", {"level": "success", "text": f"Pre-flight done: {len(pf.nodes)} items annotated."}
        )

    def _build_stt(self) -> STTWorker:
        engine = select_engine(
            self.config.stt.engine,
            self.config.stt.model,
            self.config.stt.language,
            self.config.stt.cpu_threads,
        )
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
        # Start after a Finish is a second take of the same episode, not a second
        # engine: tear the old pipelines down first or every utterance is captured
        # — and transcribed — twice.
        await self._teardown_capture()
        if session.status == "finished":
            self.finish_result = None
            self.finish_error = None
            self._finished = asyncio.Event()
            self.reasoner.start()
            self.store.emit("toast", {"level": "info", "text": "Recording again — the wrap-up will re-run."})
        self.stt = self._build_stt()
        self.stt.start()
        audio_dir = self.store.dir / "audio"
        for cfg in session.channels:
            recorder = None
            native = None
            record_this = self.config.audio.record and cfg.record and not cfg.source.startswith("file:")
            if record_this:
                recorder = WavRecorder(WavRecorder.unique_path(audio_dir, cfg.name.lower()))
                recorder.open()
                if self.config.audio.record_native and cfg.source.startswith("device:"):
                    native = WavRecorder(WavRecorder.unique_path(audio_dir, f"{cfg.name.lower()}-native"))
            pipeline = ChannelPipeline(
                cfg,
                self.config,
                self.clock.now,
                self._on_utterance,
                recorder,
                speed=self.replay_speed,
                on_finished=self._on_source_finished,
                native_recorder=native,
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
        # Only now is anything actually being recorded, so only now does time pass.
        self.clock.resume()
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
            # Nothing is being recorded from here on; Record again resumes the clock.
            self.clock.pause()
            session.duration_s = max(session.duration_s, now, self.transcript.duration())
            for pipeline in self.channels:
                await asyncio.to_thread(pipeline.stop)
            if self.stt is not None:
                await asyncio.to_thread(self.stt.stop, True)
            await self.reasoner.stop()
            paths: dict[str, str] = {}
            if not self.transcript.segments:
                # An empty transcript would still cost a wrap-up call and produce notes
                # about nothing. Say so instead.
                self.finish_error = "nothing was transcribed, so there are no show notes to write"
                log.warning("finish: %s", self.finish_error)
            else:
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
            session.status = "finished"
            self.store.mark_dirty()
            self.store.snapshot(force=True)
            self.store.log_event("finish", now, paths=paths, error=self.finish_error)
            self.store.emit(
                "done",
                {
                    "paths": paths,
                    "dir": str(self.store.dir.resolve()),
                    "duration_s": session.duration_s,
                    "cost_usd": session.usage.cost_usd,
                    "ticks": session.usage.ticks,
                    "error": self.finish_error,
                },
            )
            self._finished.set()
            return paths

    def finish_in_background(self) -> asyncio.Task[dict[str, str]]:
        """Finish without blocking the caller (the WebSocket handler).

        The task is kept on the engine: a bare ``create_task`` can be garbage-collected
        in the middle of the wrap-up.
        """
        if self._finish_task is None or self._finish_task.done():
            self._finish_task = asyncio.create_task(self.finish(), name="finish")
        return self._finish_task

    async def _teardown_capture(self, *, drain: bool = False) -> None:
        """Stop and forget every pipeline. Safe to call when nothing is running."""
        for pipeline in self.channels:
            try:
                await asyncio.to_thread(pipeline.stop)
            except Exception:  # pragma: no cover - stopping must never raise
                log.debug("error stopping a pipeline", exc_info=True)
        self.channels.clear()
        if self.stt is not None:
            await asyncio.to_thread(self.stt.stop, drain)
            self.stt = None

    async def shutdown(self) -> None:
        await self._teardown_capture()
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

    def set_ticks(self, *, interval_s: float, min_new_words: int, burst_words: int) -> None:
        """Retune the tick loop live (FR-35).

        The reasoner reads these off the config on every pass, so the change lands
        on the next tick without restarting anything.
        """
        llm = self.config.llm
        llm.tick_interval_s = float(interval_s)
        llm.min_new_words = int(min_new_words)
        llm.burst_words = int(burst_words)
        self.store.log_event(
            "ticks",
            self.clock.now(),
            interval_s=llm.tick_interval_s,
            min_new_words=llm.min_new_words,
            burst_words=llm.burst_words,
        )
        self.store.emit(
            "toast",
            {
                "level": "success",
                "text": (
                    f"Ticking every {llm.tick_interval_s:g}s past {llm.min_new_words} new words "
                    f"({llm.burst_words} for an immediate one)."
                ),
            },
        )
        log.info(
            "tick loop retuned: %.0fs / %d words / burst %d",
            llm.tick_interval_s,
            llm.min_new_words,
            llm.burst_words,
        )

    def set_language(self, language: str | None) -> None:
        """Lock (or release) the transcription language, live (FR-34).

        The STT worker reads ``language`` once per utterance, so this takes effect
        on the next thing anyone says — no restart, no lost audio.
        """
        code = None if not language or language == "auto" else language.strip().lower()
        self.config.stt.language = code or "auto"
        session = self.store.session
        session.language = code or session.language
        if code:
            # A locked language is the host's word, not a vote. Stop the ballot so
            # the reasoner cannot drift it back (SPEC §9.3 rule 4).
            session.language_votes = {code: 999}
        else:
            session.language_votes = {}
        forced = True
        if self.stt is not None:
            self.stt.set_language(code)
            forced = self.stt.engine.can_force_language
        self.store.mark_dirty()
        self.store.log_event("language", self.clock.now(), language=code or "auto")
        self.store.apply_patch(Patch(language=code or ""))
        if code and not forced:
            engine_name = self.stt.engine.name if self.stt else self.config.stt.engine
            self.store.emit(
                "toast",
                {
                    "level": "warn",
                    "text": (
                        f"{engine_name} detects the language itself and cannot be forced. "
                        f"Prompts and notes will use {code}; wrong-alphabet lines are dropped. "
                        "For a real lock, restart with --set stt.engine=faster-whisper."
                    ),
                },
            )
        elif code:
            self.store.emit("toast", {"level": "success", "text": f"Transcribing as {code}."})
        else:
            self.store.emit("toast", {"level": "info", "text": "Language detection back to auto."})

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

    def _stt_preview(self) -> dict[str, Any]:
        """What the STT layer will be, before a worker exists to ask.

        Building an engine object does not load any weights, so this is cheap; the
        answer is cached because the status payload goes out five times a second.
        """
        if self._stt_facts is None:
            name, can_force = self.config.stt.engine, None
            try:
                probe = select_engine(
                    self.config.stt.engine,
                    self.config.stt.model,
                    self.config.stt.language,
                    self.config.stt.cpu_threads,
                )
                name, can_force = probe.name, probe.can_force_language
            except Exception:  # pragma: no cover - a missing extra is reported at Start
                log.debug("cannot preview the STT engine", exc_info=True)
            self._stt_facts = (name, can_force)
        name, can_force = self._stt_facts
        return {
            "engine": name,
            "queue": 0,
            "language": self.config.stt.language,
            "can_force_language": can_force,
        }

    def status_payload(self) -> dict[str, Any]:
        return {
            "audio": {p.cfg.name: round(p.level_db, 1) for p in self.channels},
            "stt": self.stt.status() if self.stt else self._stt_preview(),
            "llm": self.reasoner.status.as_dict()
            | {
                "interval_s": self.config.llm.tick_interval_s,
                "min_new_words": self.config.llm.min_new_words,
                "burst_words": self.config.llm.burst_words,
            },
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
