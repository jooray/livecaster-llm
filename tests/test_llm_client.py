"""Venice client behaviour with a stubbed transport (M2)."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from livecaster.llm.client import CallLog, LLMClient, LLMError, MissingAPIKey, api_key
from livecaster.llm.mock import MockLLM
from livecaster.llm.pricing import PRICING, estimate_cost, price_for, refresh_from_models
from livecaster.llm.schemas import TickResult


def _response(payload: dict, cached: int = 0, prompt: int = 1000, completion: int = 200) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": json.dumps(payload)}}],
            "usage": {
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "prompt_tokens_details": {"cached_tokens": cached},
            },
        },
    )


VALID_TICK = {
    "language": "sk",
    "current": {"node_id": "T5", "summary": "úvod"},
    "covered": [{"id": "T5", "confidence": 0.9, "evidence": "e", "t": 10.0}],
    "touched": [],
    "hot": [],
    "questions": [],
    "mentions": [],
    "new_topics": [],
}


def _client(handler, tmp_path: Path | None = None) -> LLMClient:
    client = LLMClient(
        "https://api.venice.ai/api/v1",
        call_log=CallLog(tmp_path / "llm.jsonl" if tmp_path else None),
        key="test-key",
    )
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url=client.base_url,
        headers={"Authorization": "Bearer test-key"},
    )
    return client


async def test_successful_call_parses_and_prices(tmp_path: Path):
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return _response(VALID_TICK, cached=800)

    client = _client(handler, tmp_path)
    result, usage = await client.complete_json(
        "tick", [{"role": "user", "content": "x"}], model="deepseek-v4-flash-0731", cache_key="sess"
    )
    assert isinstance(result, TickResult)
    assert result.covered[0].id == "T5"
    assert usage.cached_tokens == 800
    assert usage.cost_usd == pytest.approx(estimate_cost("deepseek-v4-flash-0731", 1000, 800, 200))

    body = seen[0]
    assert body["response_format"]["json_schema"]["strict"] is True
    assert body["prompt_cache_key"] == "sess"
    assert body["venice_parameters"] == {
        "include_venice_system_prompt": False,
        "strip_thinking_response": True,
        "enable_web_search": "off",
    }
    assert body["reasoning_effort"] == "low"
    assert body["max_completion_tokens"] == 1500

    logged = json.loads((tmp_path / "llm.jsonl").read_text().strip())
    assert logged["kind"] == "tick" and logged["error"] is None
    await client.aclose()


async def test_reasoning_effort_none_is_sent_explicitly():
    """Omitting the field lets DeepSeek reason until the token budget is gone."""
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return _response(VALID_TICK)

    client = _client(handler)
    await client.complete_json("tick", [{"role": "user", "content": "x"}], model="m", effort="none")
    assert seen[0]["reasoning_effort"] == "none"
    await client.aclose()


async def test_web_search_is_opt_in():
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return _response({"links": []})

    client = _client(handler)
    await client.complete_json("links", [{"role": "user", "content": "x"}], model="m", web_search=True)
    assert seen[0]["venice_parameters"]["enable_web_search"] == "on"
    assert seen[0]["venice_parameters"]["enable_web_citations"] is True
    await client.aclose()


async def test_invalid_json_is_retried_once_then_raises(tmp_path: Path):
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "{not json"}}], "usage": {}})

    client = _client(handler, tmp_path)
    with pytest.raises(LLMError, match="invalid JSON"):
        await client.complete_json("tick", [{"role": "user", "content": "x"}], model="m")
    assert len(calls) == 2
    assert "did not validate" in calls[1]["messages"][-1]["content"]
    await client.aclose()


async def test_retry_succeeds_on_the_second_attempt():
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] == 1:
            return httpx.Response(200, json={"choices": [{"message": {"content": "{"}}], "usage": {}})
        return _response(VALID_TICK)

    client = _client(handler)
    result, _ = await client.complete_json("tick", [{"role": "user", "content": "x"}], model="m")
    assert result.language == "sk"
    await client.aclose()


async def test_http_errors_become_llm_errors(tmp_path: Path):
    client = _client(lambda request: httpx.Response(429, text="rate limited"), tmp_path)
    with pytest.raises(LLMError, match="429"):
        await client.complete_json("tick", [{"role": "user", "content": "x"}], model="m")
    logged = json.loads((tmp_path / "llm.jsonl").read_text().strip())
    assert "rate limited" in logged["error"]
    await client.aclose()


async def test_network_errors_become_llm_errors():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route")

    client = _client(handler)
    with pytest.raises(LLMError, match="ConnectError"):
        await client.complete_json("tick", [{"role": "user", "content": "x"}], model="m")
    await client.aclose()


async def test_unknown_schema_kind():
    client = _client(lambda request: _response(VALID_TICK))
    with pytest.raises(LLMError, match="unknown schema"):
        await client.complete_json("nope", [], model="m")
    await client.aclose()


def test_missing_api_key(monkeypatch):
    monkeypatch.delenv("VENICE_API_KEY", raising=False)
    with pytest.raises(MissingAPIKey):
        api_key()


# --- pricing --------------------------------------------------------------


def test_pricing_table_matches_the_spec():
    assert price_for("deepseek-v4-flash-0731") == PRICING["deepseek-v4-flash-0731"]
    p = price_for("deepseek-v4-flash-0731")
    assert (p.input, p.cached_input, p.output) == (0.175, 0.035, 0.35)
    assert price_for("deepseek-v4-pro-0813").output == 4.95
    assert price_for("something-unknown").input == 0.175  # falls back to flash


def test_cost_uses_the_cached_rate_for_cached_tokens():
    full = estimate_cost("deepseek-v4-flash-0731", 8000, 0, 800)
    cached = estimate_cost("deepseek-v4-flash-0731", 8000, 4000, 800)
    assert cached < full
    assert cached == pytest.approx((4000 * 0.175 + 4000 * 0.035 + 800 * 0.35) / 1e6)


def test_two_hour_episode_stays_under_a_dollar():
    ticks = 290
    per_tick = estimate_cost("deepseek-v4-flash-0731", 8000, 4000, 800)
    wrapup = estimate_cost("deepseek-v4-flash-0731", 40_000, 0, 4000)
    assert ticks * per_tick + wrapup < 1.0


def test_refresh_from_models_accepts_a_few_shapes():
    updated = refresh_from_models(
        {
            "data": [
                {"id": "x-model", "model_spec": {"pricing": {"input": {"usd": 1.0}, "output": {"usd": 2.0}}}},
                {"id": "y-model", "pricing": {"input": 3.0, "output": 4.0}},
                {"id": "z-model"},
            ]
        }
    )
    assert set(updated) == {"x-model", "y-model"}
    assert price_for("x-model").input == 1.0
    assert price_for("y-model").output == 4.0


# --- mock -----------------------------------------------------------------


async def test_mock_cycles_through_the_fixtures(fixtures: Path):
    mock = MockLLM(fixtures / "tick_responses")
    seen = []
    for _ in range(len(mock._files)):
        try:
            result, usage = await mock.complete_json("tick", [{"role": "user", "content": "x"}])
            seen.append(result.current.node_id)
            assert usage.cost_usd == 0.0
        except LLMError:
            seen.append("invalid")
    assert "invalid" in seen
    assert len(seen) == len(mock._files)


async def test_mock_serves_the_final_and_preflight_fixtures(fixtures: Path):
    mock = MockLLM(fixtures / "tick_responses")
    final, _ = await mock.complete_json("final", [{"role": "user", "content": "x"}])
    assert final.language == "sk" and final.titles
    preflight, _ = await mock.complete_json("preflight", [{"role": "user", "content": "x"}])
    assert preflight.nodes[0].triggers
