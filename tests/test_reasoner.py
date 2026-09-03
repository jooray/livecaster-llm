"""Tick scheduling, backoff and prompt caching (FR-17, FR-21, M2)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from livecaster.config import Config
from livecaster.engine import Engine
from livecaster.llm.client import CallUsage, LLMError
from livecaster.llm.schemas import TickCurrent, TickResult
from livecaster.session.models import Segment
from livecaster.timeutil import ManualClock


class ScriptedLLM:
    """Returns queued results (or raises queued exceptions) and records the prompts."""

    def __init__(self, script: list[Any] | None = None) -> None:
        self.script = script or []
        self.calls: list[list[dict[str, str]]] = []
        self.base_url = "scripted://"

    async def aclose(self) -> None:
        return None

    async def complete_json(self, kind: str, messages, **kwargs):
        self.calls.append(messages)
        item = self.script.pop(0) if self.script else TickResult(current=TickCurrent())
        if isinstance(item, Exception):
            raise item
        return item, CallUsage(model="scripted", prompt_tokens=100, cached_tokens=40, completion_tokens=10)


@pytest.fixture
def engine(store, config: Config):
    clock = ManualClock()
    return Engine(store, config, ScriptedLLM(), clock=clock)


def add(engine: Engine, n: int, words: int = 10, t0: float = 0.0, prefix: str = "a") -> None:
    for i in range(n):
        engine.add_segment(
            Segment(
                id=engine.transcript.next_id(),
                channel="Host",
                speaker="Host",
                t0=t0 + i * 3,
                t1=t0 + i * 3 + 2,
                text=" ".join(f"{prefix}{i}_{j}" for j in range(words)),
            )
        )


async def test_tick_needs_enough_new_words(engine: Engine):
    reasoner = engine.reasoner
    assert reasoner._due(0.0) is False
    add(engine, 2)  # 20 words, below min_new_words
    assert reasoner._due(0.0) is False
    add(engine, 1)
    assert reasoner._due(0.0) is True


async def test_a_burst_ticks_early(engine: Engine):
    reasoner = engine.reasoner
    add(engine, 3)
    await reasoner.tick(10.0)
    assert reasoner._due(11.0) is False  # only a second has passed
    add(engine, 13, t0=20.0)  # 130 new words > burst_words
    assert reasoner._due(11.0) is True


async def test_interval_gates_the_next_tick(engine: Engine):
    reasoner = engine.reasoner
    add(engine, 3)
    await reasoner.tick(10.0)
    add(engine, 3, t0=20.0)
    assert reasoner._due(20.0) is False
    assert reasoner._due(36.0) is True


async def test_tick_now_overrides_everything(engine: Engine):
    reasoner = engine.reasoner
    engine.request_tick()
    assert reasoner._due(0.0) is True


async def test_system_message_is_stable_across_ticks(engine: Engine):
    add(engine, 4)
    await engine.reasoner.tick(10.0)
    add(engine, 4, t0=20.0)
    await engine.reasoner.tick(40.0)
    client: ScriptedLLM = engine.client
    assert client.calls[0][0]["content"] == client.calls[1][0]["content"]
    assert client.calls[0][1]["content"] != client.calls[1][1]["content"]


async def test_new_part_marker_moves_with_the_last_tick(engine: Engine):
    add(engine, 4, prefix="old")
    await engine.reasoner.tick(10.0)
    add(engine, 4, t0=20.0, prefix="new")
    await engine.reasoner.tick(40.0)
    client: ScriptedLLM = engine.client
    second = client.calls[1][1]["content"]
    older, new_part = second.split("--- NEW SINCE LAST TICK ---")
    assert "old0_0" in older
    assert "old0_0" not in new_part
    assert "new3_0" in new_part


async def test_usage_accumulates(engine: Engine):
    add(engine, 4)
    await engine.reasoner.tick(10.0)
    add(engine, 4, t0=20.0)
    await engine.reasoner.tick(40.0)
    usage = engine.store.session.usage
    assert usage.ticks == 2
    assert usage.prompt_tokens == 200
    assert usage.cached_tokens == 80
    assert usage.by_model["scripted"].calls == 2


async def test_failures_back_off_exponentially(engine: Engine):
    engine.client.script = [LLMError("boom"), LLMError("boom"), LLMError("boom")]
    add(engine, 4)
    await engine.reasoner.tick(10.0)
    first = engine.reasoner.backoff_until
    await engine.reasoner.tick(11.0)
    second = engine.reasoner.backoff_until
    await engine.reasoner.tick(12.0)
    third = engine.reasoner.backoff_until
    assert second - first > 4  # 5s -> 10s -> 20s
    assert third - second > 8
    assert engine.reasoner.status.state == "error"
    assert engine.store.session.usage.failures == 3


async def test_a_failed_tick_does_not_move_the_marker(engine: Engine):
    engine.client.script = [LLMError("boom")]
    add(engine, 4)
    await engine.reasoner.tick(10.0)
    assert engine.reasoner.last_tick_segment_id is None
    assert engine.reasoner.last_tick_at is None


async def test_ticks_never_overlap(engine: Engine):
    add(engine, 4)
    engine.reasoner.in_flight = True
    assert await engine.reasoner.tick(10.0) is None
    assert engine.client.calls == []


async def test_tick_with_no_transcript_is_skipped(engine: Engine):
    assert await engine.reasoner.tick(10.0) is None
    assert engine.client.calls == []


async def test_status_payload_shape(engine: Engine):
    add(engine, 4)
    await engine.reasoner.tick(10.0)
    status = engine.status_payload()
    assert status["llm"]["state"] == "ok"
    assert status["llm"]["ticks"] == 1
    assert status["llm"]["last_latency_ms"] is not None
    assert status["session_status"] == "idle"
    assert status["clock"] == 0.0


async def test_scheduler_loop_ticks_when_due(store, config: Config):
    config.llm.tick_interval_s = 0.0
    clock = ManualClock()
    engine = Engine(store, config, ScriptedLLM(), clock=clock)
    store.session.status = "running"
    add(engine, 4)
    task = engine.reasoner.start()
    await asyncio.sleep(1.4)
    engine.reasoner.running = False
    task.cancel()
    assert engine.store.session.usage.ticks >= 1
