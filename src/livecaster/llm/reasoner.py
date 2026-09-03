"""Tick scheduler: one LLM call every ``tick_interval_s`` (FR-17, PLAN §5.1)."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from livecaster.config import Config
from livecaster.llm.prompts import build_tick_system, build_tick_user
from livecaster.llm.schemas import TickResult
from livecaster.log import get_logger
from livecaster.session.models import Patch
from livecaster.session.reducer import apply_tick
from livecaster.session.store import SessionStore
from livecaster.session.transcript import Transcript

log = get_logger(__name__)

MAX_BACKOFF_S = 120.0
BASE_BACKOFF_S = 5.0


class CompletionClient(Protocol):  # pragma: no cover - structural typing only
    async def complete_json(self, kind: str, messages: list[dict[str, str]], **kwargs: Any) -> Any: ...
    async def aclose(self) -> None: ...


@dataclass
class ReasonerStatus:
    state: str = "idle"  # idle | ticking | ok | error | backoff
    last_tick_t: float | None = None
    last_latency_ms: int | None = None
    next_in_s: float | None = None
    error: str | None = None
    ticks: int = 0
    failures: int = 0
    latencies_ms: list[int] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "last_tick_t": self.last_tick_t,
            "last_latency_ms": self.last_latency_ms,
            "next_in_s": None if self.next_in_s is None else round(self.next_in_s, 1),
            "error": self.error,
            "ticks": self.ticks,
            "failures": self.failures,
            "latencies_ms": self.latencies_ms[-20:],
        }


class Reasoner:
    def __init__(
        self,
        store: SessionStore,
        transcript: Transcript,
        client: CompletionClient,
        config: Config,
        clock: Any,
    ) -> None:
        self.store = store
        self.transcript = transcript
        self.client = client
        self.config = config
        self.clock = clock
        self.status = ReasonerStatus()
        self.running = False
        self.in_flight = False
        self.tick_requested = False
        self.last_tick_at: float | None = None
        self.last_tick_segment_id: str | None = None
        self.backoff_until = 0.0
        self.failures = 0
        self._system_message: str | None = None
        self._task: asyncio.Task[None] | None = None

    # --- prompt ------------------------------------------------------------

    def system_message(self) -> str:
        """Built once per outline so the cached prefix stays byte-identical."""
        if self._system_message is None:
            self._system_message = build_tick_system(self.store.outline, self.store.session.language)
        return self._system_message

    def invalidate_prompt(self) -> None:
        self._system_message = None

    @property
    def show_speakers(self) -> bool:
        return len(self.store.session.channels) > 1

    # --- scheduling --------------------------------------------------------

    def start(self) -> asyncio.Task[None]:
        self.running = True
        self._task = asyncio.create_task(self.run(), name="reasoner")
        return self._task

    async def stop(self) -> None:
        self.running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: B014
                pass
            self._task = None

    def request_tick(self) -> None:
        self.tick_requested = True

    def _due(self, now: float) -> bool:
        cfg = self.config.llm
        new_words = self.transcript.words_since(self.last_tick_segment_id)
        if self.tick_requested:
            return True
        if new_words >= cfg.burst_words:
            return True
        if self.last_tick_at is None:
            return new_words >= cfg.min_new_words
        elapsed = now - self.last_tick_at
        return elapsed >= cfg.tick_interval_s and new_words >= cfg.min_new_words

    async def run(self) -> None:
        while self.running:
            await asyncio.sleep(1.0)
            now = self.clock.now()
            if self.store.session.status != "running" or self.in_flight:
                continue
            if not self._due(now):
                if self.last_tick_at is not None:
                    self.status.next_in_s = max(
                        0.0, self.config.llm.tick_interval_s - (now - self.last_tick_at)
                    )
                continue
            if time.monotonic() < self.backoff_until:
                self.status.state = "backoff"
                continue
            await self.tick(now)

    # --- one tick ----------------------------------------------------------

    async def tick(self, now: float | None = None) -> Patch | None:
        if self.in_flight:
            return None
        now = self.clock.now() if now is None else now
        cfg = self.config.llm
        self.in_flight = True
        self.status.state = "ticking"
        started = time.monotonic()
        try:
            segments = self.transcript.window(cfg.transcript_window_words, self.last_tick_segment_id)
            if not segments:
                return None
            new_part = self.transcript.new_since(self.last_tick_segment_id)
            new_part_start_t = new_part[0].t0 if new_part else None
            hot_last = [r.node_id for r in self.store.session.suggestions.next]
            messages = [
                {"role": "system", "content": self.system_message()},
                {
                    "role": "user",
                    "content": build_tick_user(
                        self.store.session,
                        self.store.outline,
                        segments,
                        self.last_tick_segment_id,
                        now,
                        show_speakers=self.show_speakers,
                        hot_last=hot_last,
                        window_words=cfg.transcript_window_words,
                    ),
                },
            ]
            result, usage = await self.client.complete_json(
                "tick",
                messages,
                model=cfg.tick_model,
                effort=cfg.reasoning_effort_tick,
                max_tokens=cfg.max_tick_tokens,
                temperature=cfg.temperature,
                cache_key=self.store.session.id,
                timeout=cfg.tick_timeout_s,
            )
            assert isinstance(result, TickResult)
            session = self.store.session
            session.usage.ticks += 1
            session.usage.add(
                usage.model, usage.prompt_tokens, usage.cached_tokens, usage.completion_tokens, usage.cost_usd
            )
            patch = apply_tick(
                session,
                result,
                now,
                self.store.outline,
                cfg,
                new_part_start_t=new_part_start_t,
                window_start_t=segments[0].t0 if segments else None,
                language_locked=self.config.stt.language != "auto",
            )
            self.last_tick_segment_id = self.transcript.last_id()
            self.last_tick_at = now
            self.failures = 0
            latency_ms = int((time.monotonic() - started) * 1000)
            self.status.state = "ok"
            self.status.error = None
            self.status.last_tick_t = now
            self.status.last_latency_ms = latency_ms
            self.status.next_in_s = cfg.tick_interval_s
            self.status.ticks += 1
            self.status.latencies_ms.append(latency_ms)
            self.store.apply_patch(patch)
            return patch
        except Exception as exc:
            self.failures += 1
            self.store.session.usage.failures += 1
            self.status.failures += 1
            self.status.state = "error"
            self.status.error = f"{type(exc).__name__}: {exc}"[:300]
            self.backoff_until = time.monotonic() + min(
                MAX_BACKOFF_S, BASE_BACKOFF_S * 2 ** (self.failures - 1)
            )
            log.warning("tick failed (%s), backing off", self.status.error)
            self.store.emit("llm_error", self.status.as_dict())
            return None
        finally:
            self.in_flight = False
            self.tick_requested = False
