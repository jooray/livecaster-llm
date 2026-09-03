"""Provider routing and per-dialect schema narrowing."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from livecaster.config import Config, ProviderConfig
from livecaster.llm.client import CallLog, LLMClient
from livecaster.llm.providers import ClientPool, make_client, provider_config, split_model
from livecaster.llm.schemas import (
    ANTHROPIC_UNSUPPORTED_KEYWORDS,
    FINAL_SCHEMA,
    TICK_SCHEMA,
    response_format,
    schema_dialect,
    schema_for,
)

# --- model specs ----------------------------------------------------------


@pytest.mark.parametrize(
    "spec,expected",
    [
        ("claude-sonnet-5", ("venice", "claude-sonnet-5")),
        ("deepseek-v4-flash-0731-fast", ("venice", "deepseek-v4-flash-0731-fast")),
        ("anthropic:claude-sonnet-5", ("anthropic", "claude-sonnet-5")),
        ("openai:gpt-5", ("openai", "gpt-5")),
        ("  ANTHROPIC : claude-opus-5 ", ("anthropic", "claude-opus-5")),
    ],
)
def test_split_model(spec: str, expected: tuple[str, str]):
    assert split_model(spec, "venice") == expected


def test_default_provider_is_configurable():
    assert split_model("claude-sonnet-5", "anthropic") == ("anthropic", "claude-sonnet-5")


# --- schema dialects ------------------------------------------------------


@pytest.mark.parametrize(
    "model,dialect",
    [
        ("claude-sonnet-5", "anthropic"),
        ("claude-opus-5", "anthropic"),
        ("claude-fable-5-1", "anthropic"),
        ("anthropic:claude-haiku-4-5", "anthropic"),
        ("deepseek-v4-flash-0731", "openai"),
        ("openai-gpt-56-terra", "openai"),
    ],
)
def test_schema_dialect(model: str, dialect: str):
    assert schema_dialect(model) == dialect


def test_anthropic_dialect_drops_only_the_unsupported_keywords():
    """Venice forwards `response_format` to Anthropic, which rejects `maxItems`."""
    narrowed = json.dumps(schema_for("final", "anthropic"))
    for keyword in ANTHROPIC_UNSUPPORTED_KEYWORDS:
        assert keyword not in narrowed
    # Everything else the schema relies on survives.
    assert "maxLength" in narrowed
    assert "additionalProperties" in narrowed
    assert '"enum"' in narrowed
    assert "required" in narrowed


def test_openai_dialect_is_the_untouched_schema():
    assert schema_for("tick", "openai") == TICK_SCHEMA
    assert schema_for("final", "openai") == FINAL_SCHEMA
    assert "maxItems" in json.dumps(schema_for("tick", "openai"))


def test_narrowing_does_not_mutate_the_module_schema():
    before = json.dumps(FINAL_SCHEMA)
    schema_for("final", "anthropic")
    assert json.dumps(FINAL_SCHEMA) == before


def test_response_format_carries_the_dialect():
    rf = response_format("tick", "anthropic")
    assert rf["json_schema"]["strict"] is True
    assert "maxItems" not in json.dumps(rf)


def test_narrowed_schema_still_forbids_extra_properties():
    def walk(node: Any):
        if isinstance(node, dict):
            yield node
            for v in node.values():
                yield from walk(v)
        elif isinstance(node, list):
            for v in node:
                yield from walk(v)

    for obj in walk(schema_for("final", "anthropic")):
        if obj.get("type") == "object":
            assert obj.get("additionalProperties") is False
            assert set(obj.get("required", [])) == set(obj.get("properties", {}))


# --- client construction --------------------------------------------------


def test_make_client_kinds(config: Config):
    venice = make_client(config, "venice")
    assert isinstance(venice, LLMClient)
    assert venice.venice_extensions is True
    assert venice.api_key_env == "VENICE_API_KEY"

    plain = make_client(config, "openai")
    assert isinstance(plain, LLMClient)
    assert plain.venice_extensions is False
    assert plain.base_url.endswith("openai.com/v1")

    from livecaster.llm.anthropic_client import AnthropicClient

    assert isinstance(make_client(config, "anthropic"), AnthropicClient)


def test_unknown_provider_is_named_clearly(config: Config):
    with pytest.raises(ValueError, match="unknown LLM provider 'moon'"):
        provider_config(config, "moon")


def test_unknown_provider_kind(config: Config):
    config.llm.providers["weird"] = ProviderConfig(kind="openai")
    config.llm.providers["weird"].kind = "telepathy"  # type: ignore[assignment]
    with pytest.raises(ValueError, match="unknown kind"):
        make_client(config, "weird")


# --- pool -----------------------------------------------------------------


class Recorder:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.closed = False
        self.call_log = CallLog(None)

    async def complete_json(self, kind: str, messages, **kwargs):
        self.calls.append({"kind": kind, **kwargs})
        return {"ok": True}, None

    async def aclose(self) -> None:
        self.closed = True


async def test_pool_routes_by_prefix_and_reuses_clients(config: Config, monkeypatch):
    made: list[str] = []

    def fake_make(cfg, provider, **kwargs):
        made.append(provider)
        return Recorder()

    monkeypatch.setattr("livecaster.llm.providers.make_client", fake_make)
    pool = ClientPool(config)

    await pool.complete_json("tick", [], model="deepseek-v4-flash-0731-fast")
    await pool.complete_json("tick", [], model="deepseek-v4-flash-0731-fast")
    await pool.complete_json("final", [], model="anthropic:claude-sonnet-5")
    assert made == ["venice", "anthropic"]
    assert pool.providers_in_use == ["anthropic", "venice"]

    venice, model = pool.resolve("claude-sonnet-5")
    assert model == "claude-sonnet-5"  # bare name stays on the default provider
    assert venice.calls[0]["model"] == "deepseek-v4-flash-0731-fast"

    anthro, _ = pool.resolve("anthropic:claude-sonnet-5")
    assert anthro.calls[0]["model"] == "claude-sonnet-5"  # prefix stripped

    await pool.aclose()
    assert venice.closed and anthro.closed
    assert pool.providers_in_use == []


async def test_pool_closing_survives_a_broken_client(config: Config, monkeypatch):
    class Broken(Recorder):
        async def aclose(self) -> None:
            raise RuntimeError("nope")

    monkeypatch.setattr("livecaster.llm.providers.make_client", lambda *a, **k: Broken())
    pool = ClientPool(config)
    await pool.complete_json("tick", [], model="x")
    await pool.aclose()  # must not raise


async def test_pool_propagates_a_late_call_log(tmp_path, config: Config, monkeypatch):
    monkeypatch.setattr("livecaster.llm.providers.make_client", lambda *a, **k: Recorder())
    pool = ClientPool(config)
    await pool.complete_json("tick", [], model="x")
    client, _ = pool.resolve("x")

    pool.set_call_log(CallLog(tmp_path / "llm.jsonl"))
    assert pool.call_log.path == tmp_path / "llm.jsonl"
    assert client.call_log.path == tmp_path / "llm.jsonl"


# --- the OpenAI-compatible client, per provider ---------------------------


def _stub(handler, **kwargs) -> LLMClient:
    client = LLMClient("https://example.test/v1", key="k", **kwargs)
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=client.base_url)
    return client


def _ok(payload: dict) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": json.dumps(payload)}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "prompt_tokens_details": {}},
        },
    )


VALID_TICK = {
    "language": "sk",
    "current": {"node_id": None, "summary": ""},
    "covered": [],
    "touched": [],
    "hot": [],
    "questions": [],
    "mentions": [],
    "new_topics": [],
}


async def test_venice_extensions_are_off_for_a_plain_endpoint():
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return _ok(VALID_TICK)

    client = _stub(handler, venice_extensions=False)
    await client.complete_json("tick", [{"role": "user", "content": "x"}], model="gpt-5")
    assert "venice_parameters" not in seen[0]
    await client.aclose()

    client = _stub(handler, venice_extensions=True)
    await client.complete_json("tick", [{"role": "user", "content": "x"}], model="deepseek-v4-flash-0731")
    assert seen[1]["venice_parameters"]["include_venice_system_prompt"] is False
    await client.aclose()


async def test_claude_via_venice_gets_the_narrowed_schema():
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return _ok(VALID_TICK)

    client = _stub(handler)
    await client.complete_json("tick", [{"role": "user", "content": "x"}], model="claude-sonnet-5")
    assert "maxItems" not in json.dumps(seen[0]["response_format"])
    await client.complete_json("tick", [{"role": "user", "content": "x"}], model="deepseek-v4-flash-0731")
    assert "maxItems" in json.dumps(seen[1]["response_format"])
    await client.aclose()


@pytest.mark.parametrize(
    "effort,sent",
    [
        ("none", "none"),
        ("low", "low"),
        ("medium", "low"),
        ("high", "high"),
        ("xhigh", "high"),
        ("max", "high"),
    ],
)
async def test_effort_is_narrowed_for_venice(effort: str, sent: str):
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return _ok(VALID_TICK)

    client = _stub(handler)
    await client.complete_json("tick", [{"role": "user", "content": "x"}], model="m", effort=effort)
    assert seen[0]["reasoning_effort"] == sent
    await client.aclose()


def test_api_key_env_is_per_provider(monkeypatch):
    from livecaster.llm.client import MissingAPIKey, api_key

    monkeypatch.setenv("SOME_OTHER_KEY", "abc")
    monkeypatch.delenv("VENICE_API_KEY", raising=False)
    assert api_key("SOME_OTHER_KEY") == "abc"
    with pytest.raises(MissingAPIKey, match="VENICE_API_KEY is not set"):
        api_key("VENICE_API_KEY")
