"""The direct Anthropic path (only used when a model is prefixed `anthropic:`)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from livecaster.llm.anthropic_client import STREAM_ABOVE_TOKENS, AnthropicClient, _split_system
from livecaster.llm.client import CallLog, LLMError
from livecaster.llm.schemas import TickResult

VALID_TICK = {
    "language": "sk",
    "current": {"node_id": "T5", "summary": "úvod"},
    "covered": [{"id": "T5", "confidence": 0.9, "evidence": "e", "t": 12.0}],
    "touched": [],
    "hot": [],
    "questions": [],
    "mentions": [],
    "new_topics": [],
}


class FakeBlock:
    def __init__(self, text: str) -> None:
        self.type = "text"
        self.text = text


class FakeUsage:
    def __init__(self, fresh=100, cached=800, written=0, output=200) -> None:
        self.input_tokens = fresh
        self.cache_read_input_tokens = cached
        self.cache_creation_input_tokens = written
        self.output_tokens = output


class FakeMessage:
    def __init__(self, payload: Any, stop_reason: str = "end_turn", usage: FakeUsage | None = None) -> None:
        text = payload if isinstance(payload, str) else json.dumps(payload)
        self.content = [FakeBlock(text)]
        self.stop_reason = stop_reason
        self.stop_details = None
        self.usage = usage or FakeUsage()

    def to_json(self) -> str:
        return json.dumps({"stop_reason": self.stop_reason})


class FakeStream:
    def __init__(self, message: Any) -> None:
        self._message = message

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get_final_message(self):
        if isinstance(self._message, Exception):
            raise self._message
        return self._message


class FakeMessages:
    def __init__(self, script: list[Any]) -> None:
        self.script = script
        self.created: list[dict] = []
        self.streamed: list[dict] = []

    def _next(self):
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def create(self, **kwargs):
        self.created.append(kwargs)
        return self._next()

    def stream(self, **kwargs):
        self.streamed.append(kwargs)
        return FakeStream(self.script.pop(0))


class FakeSDK:
    def __init__(self, script: list[Any]) -> None:
        self.messages = FakeMessages(script)
        self.closed = False
        self.options: dict[str, Any] = {}

    def with_options(self, **kwargs):
        self.options.update(kwargs)
        return self

    async def close(self):
        self.closed = True


def _client(script: list[Any], tmp_path=None) -> tuple[AnthropicClient, FakeSDK]:
    sdk = FakeSDK(script)
    client = AnthropicClient(call_log=CallLog(tmp_path / "llm.jsonl" if tmp_path else None))
    client._client = sdk
    return client, sdk


# --- request shape --------------------------------------------------------


async def test_request_shape_for_the_wrapup(tmp_path):
    client, sdk = _client([FakeMessage(VALID_TICK)], tmp_path)
    result, usage = await client.complete_json(
        "tick",
        [{"role": "system", "content": "OUTLINE"}, {"role": "user", "content": "TRANSCRIPT"}],
        model="claude-sonnet-5",
        effort="high",
        max_tokens=2000,
        temperature=0.2,
    )
    assert isinstance(result, TickResult)
    assert result.covered[0].id == "T5"

    req = sdk.messages.created[0]
    # System prompt out of band, with a cache breakpoint on the stable outline block.
    assert req["system"][0]["text"] == "OUTLINE"
    assert req["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert [m["role"] for m in req["messages"]] == ["user"]
    # Structured output, not tool use.
    assert req["output_config"]["format"]["type"] == "json_schema"
    assert "maxItems" not in json.dumps(req["output_config"]["format"]["schema"])
    assert req["output_config"]["effort"] == "high"
    assert req["thinking"] == {"type": "adaptive"}
    # temperature and top_p are rejected on Sonnet 5.
    assert "temperature" not in req
    assert "top_p" not in req

    logged = json.loads((tmp_path / "llm.jsonl").read_text().strip())
    assert logged["provider"] == "anthropic" and logged["error"] is None
    assert usage.prompt_tokens == 900 and usage.cached_tokens == 800


async def test_effort_none_disables_thinking():
    client, sdk = _client([FakeMessage(VALID_TICK)])
    await client.complete_json("tick", [{"role": "user", "content": "x"}], model="m", effort="none")
    req = sdk.messages.created[0]
    assert req["thinking"] == {"type": "disabled"}
    assert "effort" not in req["output_config"]


@pytest.mark.parametrize("effort", ["low", "medium", "high", "xhigh", "max"])
async def test_all_five_effort_levels_pass_through(effort: str):
    client, sdk = _client([FakeMessage(VALID_TICK)])
    await client.complete_json("tick", [{"role": "user", "content": "x"}], model="m", effort=effort)
    assert sdk.messages.created[0]["output_config"]["effort"] == effort


async def test_large_max_tokens_streams():
    client, sdk = _client([FakeMessage(VALID_TICK)])
    await client.complete_json(
        "tick", [{"role": "user", "content": "x"}], model="m", max_tokens=STREAM_ABOVE_TOKENS + 1
    )
    assert sdk.messages.streamed and not sdk.messages.created


async def test_small_max_tokens_does_not_stream():
    client, sdk = _client([FakeMessage(VALID_TICK)])
    await client.complete_json("tick", [{"role": "user", "content": "x"}], model="m", max_tokens=1000)
    assert sdk.messages.created and not sdk.messages.streamed


async def test_web_search_declares_the_server_tool():
    client, sdk = _client([FakeMessage({"links": []})])
    await client.complete_json("links", [{"role": "user", "content": "x"}], model="m", web_search=True)
    assert sdk.messages.created[0]["tools"][0]["type"] == "web_search_20260209"


async def test_a_system_only_prompt_still_gets_a_user_turn():
    system, turns = _split_system([{"role": "system", "content": "a"}])
    assert system == "a"
    assert turns == [{"role": "user", "content": "Proceed."}]


async def test_two_system_messages_are_joined():
    system, turns = _split_system(
        [
            {"role": "system", "content": "a"},
            {"role": "user", "content": "u"},
            {"role": "system", "content": "b"},
        ]
    )
    assert system == "a\n\nb"
    assert turns == [{"role": "user", "content": "u"}]


# --- failure paths --------------------------------------------------------


async def test_refusal_becomes_an_llm_error(tmp_path):
    class Details:
        category = "cyber"

    message = FakeMessage(VALID_TICK, stop_reason="refusal")
    message.stop_details = Details()
    client, _ = _client([message], tmp_path)
    with pytest.raises(LLMError, match="declined"):
        await client.complete_json("tick", [{"role": "user", "content": "x"}], model="m")
    assert "refusal:cyber" in (tmp_path / "llm.jsonl").read_text()


async def test_empty_content_is_reported():
    client, _ = _client([FakeMessage("   ")])
    with pytest.raises(LLMError, match="empty content"):
        await client.complete_json("tick", [{"role": "user", "content": "x"}], model="m")


async def test_invalid_json_retries_once_then_raises(tmp_path):
    client, sdk = _client([FakeMessage("{not json"), FakeMessage("{still not")], tmp_path)
    with pytest.raises(LLMError, match="invalid JSON"):
        await client.complete_json("tick", [{"role": "user", "content": "x"}], model="m")
    assert len(sdk.messages.created) == 2
    assert "did not validate" in sdk.messages.created[1]["messages"][-1]["content"]


async def test_retry_succeeds_on_the_second_attempt():
    client, _ = _client([FakeMessage("{"), FakeMessage(VALID_TICK)])
    result, _ = await client.complete_json("tick", [{"role": "user", "content": "x"}], model="m")
    assert result.language == "sk"


async def test_sdk_errors_become_llm_errors(tmp_path):
    client, _ = _client([RuntimeError("connection reset")], tmp_path)
    with pytest.raises(LLMError, match="RuntimeError"):
        await client.complete_json("tick", [{"role": "user", "content": "x"}], model="m")
    assert "connection reset" in (tmp_path / "llm.jsonl").read_text()


async def test_unknown_schema_kind():
    client, _ = _client([])
    with pytest.raises(LLMError, match="unknown schema"):
        await client.complete_json("nope", [], model="m")


async def test_missing_package_is_explained(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name == "anthropic":
            raise ImportError("no module named anthropic")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked)
    client = AnthropicClient()
    with pytest.raises(LLMError, match="uv sync --extra anthropic"):
        client._sdk()


async def test_aclose_is_idempotent():
    client, sdk = _client([])
    await client.aclose()
    assert sdk.closed
    await client.aclose()


async def test_list_models_is_shaped_like_venice():
    class Model:
        id = "claude-sonnet-5"
        display_name = "Claude Sonnet 5"

    class Page:
        data = [Model()]

    class SDK:
        class models:
            @staticmethod
            async def list():
                return Page()

    client = AnthropicClient()
    client._client = SDK()
    payload = await client.list_models()
    assert payload["data"][0]["id"] == "claude-sonnet-5"


async def test_timeout_is_applied_per_call():
    client, sdk = _client([FakeMessage(VALID_TICK)])
    await client.complete_json("tick", [{"role": "user", "content": "x"}], model="m", timeout=42.0)
    assert sdk.options["timeout"] == 42.0
