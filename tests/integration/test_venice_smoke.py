"""Real Venice calls. Skipped without VENICE_API_KEY; never part of the default run."""

from __future__ import annotations

import os
import statistics
import time
from pathlib import Path

import pytest

from livecaster.config import Config
from livecaster.llm.client import CallLog, LLMClient
from livecaster.llm.prompts import build_tick_system, build_tick_user
from livecaster.llm.schemas import TickResult
from livecaster.outline.parser import parse_outline_file
from livecaster.replay import load_transcript_fixture
from livecaster.session.models import Session

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.environ.get("VENICE_API_KEY"), reason="VENICE_API_KEY is not set"),
]

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def _messages(fixture: str, outline_file: str, count: int, language: str):
    outline = parse_outline_file(FIXTURES / outline_file)
    segments = load_transcript_fixture(FIXTURES / fixture)[:count]
    session = Session(id="smoke", outline=outline.nodes, language=language)
    return outline, [
        {"role": "system", "content": build_tick_system(outline, language)},
        {
            "role": "user",
            "content": build_tick_user(session, outline, segments, None, segments[-1].t1, show_speakers=True),
        },
    ]


@pytest.fixture
def client(tmp_path: Path) -> LLMClient:
    cfg = Config()
    return LLMClient(cfg.llm.base_url, call_log=CallLog(tmp_path / "llm.jsonl"), timeout=60.0)


async def test_models_endpoint_lists_the_configured_models(client: LLMClient):
    cfg = Config()
    payload = await client.list_models()
    ids = {m["id"] for m in payload["data"]}
    assert cfg.llm.tick_model in ids, sorted(ids)
    assert cfg.llm.final_model in ids
    await client.aclose()


async def test_slovak_tick_returns_usable_json(client: LLMClient):
    cfg = Config()
    _, messages = _messages("transcript_sk.jsonl", "osnova.md", 20, "sk")
    result, usage = await client.complete_json(
        "tick",
        messages,
        model=cfg.llm.tick_model,
        effort=cfg.llm.reasoning_effort_tick,
        max_tokens=cfg.llm.max_tick_tokens,
        cache_key="smoke-sk",
        timeout=60.0,
    )
    assert isinstance(result, TickResult)
    assert result.language == "sk"
    assert result.covered or result.hot
    assert result.current.summary
    assert usage.prompt_tokens > 1000
    await client.aclose()


async def test_english_tick_stays_english(client: LLMClient):
    cfg = Config()
    _, messages = _messages("transcript_en.jsonl", "outline_en.md", 20, "en")
    result, _ = await client.complete_json(
        "tick",
        messages,
        model=cfg.llm.tick_model,
        effort=cfg.llm.reasoning_effort_tick,
        max_tokens=cfg.llm.max_tick_tokens,
        cache_key="smoke-en",
        timeout=60.0,
    )
    assert result.language == "en"
    await client.aclose()


async def test_identical_prefix_hits_the_prompt_cache(client: LLMClient):
    cfg = Config()
    _, messages = _messages("transcript_sk.jsonl", "osnova.md", 20, "sk")
    await client.complete_json(
        "tick",
        messages,
        model=cfg.llm.tick_model,
        effort=cfg.llm.reasoning_effort_tick,
        max_tokens=cfg.llm.max_tick_tokens,
        cache_key="smoke-cache",
        timeout=60.0,
    )
    _, usage = await client.complete_json(
        "tick",
        messages,
        model=cfg.llm.tick_model,
        effort=cfg.llm.reasoning_effort_tick,
        max_tokens=cfg.llm.max_tick_tokens,
        cache_key="smoke-cache",
        timeout=60.0,
    )
    assert usage.cached_tokens > 0
    await client.aclose()


@pytest.mark.slow
async def test_tick_latency_p95(client: LLMClient):
    """Records the number that decides the default `reasoning_effort_tick`."""
    cfg = Config()
    _, messages = _messages("transcript_sk.jsonl", "osnova.md", 30, "sk")
    latencies = []
    for i in range(10):
        started = time.monotonic()
        await client.complete_json(
            "tick",
            messages,
            model=cfg.llm.tick_model,
            effort=cfg.llm.reasoning_effort_tick,
            max_tokens=cfg.llm.max_tick_tokens,
            cache_key=f"smoke-lat-{i}",
            timeout=60.0,
        )
        latencies.append(time.monotonic() - started)
    latencies.sort()
    p95 = latencies[int(0.95 * (len(latencies) - 1))]
    print(f"\ntick latency: median {statistics.median(latencies):.1f}s p95 {p95:.1f}s")
    # SPEC §5: p95 <= 10 s with the configured effort, else drop to `none`.
    assert p95 < 15.0
    await client.aclose()
