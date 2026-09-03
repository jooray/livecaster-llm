"""Replay: drive the whole pipeline from a transcript, a WAV or a session (FR-32)."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path
from typing import Any

from livecaster.config import ChannelConfig, Config
from livecaster.engine import Engine
from livecaster.log import get_logger
from livecaster.session.models import Segment
from livecaster.session.store import SessionStore, create_session
from livecaster.timeutil import ManualClock

log = get_logger(__name__)


def load_transcript_fixture(path: str | Path) -> list[Segment]:
    segments: list[Segment] = []
    for i, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        data = json.loads(line)
        data.setdefault("id", f"S{i}")
        data.setdefault("channel", data.get("speaker") or "Host")
        data.setdefault("engine", "fixture")
        segments.append(Segment.model_validate(data))
    return segments


class TranscriptReplay:
    """Feeds pre-existing segments into the engine at ``speed`` × real time."""

    def __init__(self, engine: Engine, segments: list[Segment], speed: float = 1.0) -> None:
        self.engine = engine
        self.segments = sorted(segments, key=lambda s: (s.t1, s.t0))
        self.speed = speed
        self.clock: ManualClock = engine.clock  # type: ignore[assignment]
        self.done = asyncio.Event()

    async def run(self) -> None:
        session = self.engine.store.session
        session.status = "running"
        self.engine.store.emit("status_changed", "running")
        started = time.monotonic()
        for seg in self.segments:
            if self.speed > 0:
                target = started + seg.t1 / self.speed
                delay = target - time.monotonic()
                if delay > 0:
                    await asyncio.sleep(delay)
            self.clock.set(seg.t1)
            new = Segment.model_validate(seg.model_dump())
            new.id = self.engine.transcript.next_id()
            if len(session.channels) <= 1:
                new.speaker = None
            self.engine.add_segment(new)
            if self.speed == 0:
                # No wall clock to wait for: tick whenever the scheduler would have.
                await self._maybe_tick()
        session.duration_s = self.clock.now()
        self.done.set()

    async def _maybe_tick(self) -> None:
        reasoner = self.engine.reasoner
        if reasoner.in_flight:
            return
        if reasoner._due(self.clock.now()):
            await reasoner.tick(self.clock.now())


def attach_call_log(client: Any, store: SessionStore) -> None:
    """Point a client that has no log file at this session's ``llm.jsonl``."""
    from livecaster.llm.client import CallLog

    log_ = getattr(client, "call_log", None)
    if log_ is None or log_.path is not None:
        return
    setter = getattr(client, "set_call_log", None)
    if setter is not None:  # a ClientPool also updates the clients it already built
        setter(CallLog(store.llm_log_path))
    else:
        client.call_log = CallLog(store.llm_log_path)


def build_replay_session(
    outline: str | Path,
    config: Config,
    *,
    channels: list[str] | None = None,
    slug: str | None = None,
    base_dir: str | Path | None = None,
) -> SessionStore:
    cfg = config.model_copy(deep=True)
    names = channels or ["Host"]
    cfg.audio.channels = [ChannelConfig(name=n, source="file:", record=False) for n in names]
    cfg.audio.record = False
    store = create_session(outline, cfg, mode="replay", slug=slug, base_dir=base_dir)
    return store


async def replay_transcript(
    outline: str | Path,
    transcript_path: str | Path,
    config: Config,
    client: Any,
    *,
    speed: float = 0.0,
    slug: str | None = None,
    base_dir: str | Path | None = None,
    wrapup: bool = True,
    verbose: bool = True,
) -> SessionStore:
    """Replay a transcript fixture end to end and return the finished store."""
    segments = load_transcript_fixture(transcript_path)
    channels = sorted({s.channel for s in segments})
    store = build_replay_session(outline, config, channels=channels, slug=slug, base_dir=base_dir)
    for i, name in enumerate(channels):
        store.session.channels[i].is_direct = name.lower() != "host"
    attach_call_log(client, store)
    clock = ManualClock()
    engine = Engine(store, config, client, clock=clock)
    if verbose:
        _print_patches(store)
    await engine.startup()
    await engine.reasoner.stop()  # replay drives ticks itself, deterministically
    replay = TranscriptReplay(engine, segments, speed=speed)
    await replay.run()
    if speed > 0:
        await engine.reasoner.tick(clock.now())
    if wrapup:
        await engine.finish()
    else:
        store.session.status = "finished"
        store.snapshot(force=True)
    await engine.shutdown()
    return store


def _print_patches(store: SessionStore) -> None:
    from rich.console import Console

    console = Console()

    def listener(kind: str, payload: Any) -> None:
        if kind != "patch":
            return
        parts = []
        for node_id, st in payload.nodes.items():
            if st.status != "untouched":
                parts.append(f"{node_id}={st.status}")
            elif st.hot:
                parts.append(f"{node_id} hot {st.hot.score:.2f}")
        if parts:
            console.print("[dim]patch[/dim] " + " ".join(parts))

    store.subscribe(listener)


class WavReplaySession:
    """Replay a WAV through the real STT pipeline."""

    def __init__(
        self,
        outline: str | Path,
        wav: str | Path,
        config: Config,
        client: Any,
        *,
        speed: float = 1.0,
        channel_name: str = "Host",
        slug: str | None = None,
        base_dir: str | Path | None = None,
    ) -> None:
        cfg = config.model_copy(deep=True)
        cfg.audio.channels = [
            ChannelConfig(name=channel_name, source=f"file:{Path(wav).resolve()}", record=False)
        ]
        cfg.audio.record = False
        self.config = cfg
        self.store = create_session(outline, cfg, mode="replay", slug=slug, base_dir=base_dir)
        self.engine = Engine(
            self.store,
            cfg,
            client,
            replay_speed=speed,
            autostart=True,
            on_replay_finished=self._on_finished,
        )
        self._finished = threading.Event()

    def _on_finished(self) -> None:
        asyncio.ensure_future(self._finish())

    async def _finish(self) -> None:
        await asyncio.sleep(1.0)
        await self.engine.finish()
