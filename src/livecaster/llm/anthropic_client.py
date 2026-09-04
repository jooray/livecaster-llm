"""Direct Anthropic Messages API client.

The default configuration reaches Claude through Venice, so this path only runs
when a model is prefixed `anthropic:` — someone who would rather bill Anthropic
directly, or who wants a feature Venice does not proxy. It presents the same
``complete_json`` contract as the OpenAI-compatible client so the reasoner and the
wrap-up cannot tell them apart.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

from pydantic import BaseModel, ValidationError

from livecaster.llm.client import CallLog, CallUsage, LLMError, MissingAPIKey
from livecaster.llm.pricing import estimate_cost
from livecaster.llm.schemas import SCHEMAS, schema_for
from livecaster.log import get_logger

log = get_logger(__name__)

#: `effort` values the Messages API accepts, and where our own vocabulary lands.
_EFFORTS = {"low": "low", "medium": "medium", "high": "high", "xhigh": "xhigh", "max": "max"}

#: Structured outputs cannot be streamed back as a parsed object, so a large
#: wrap-up would otherwise risk the SDK's HTTP timeout. Stream above this.
STREAM_ABOVE_TOKENS = 8000


class AnthropicClient:
    """Thin wrapper over ``client.messages`` with strict JSON output."""

    def __init__(
        self,
        *,
        call_log: CallLog | None = None,
        timeout: float = 60.0,
        api_key_env: str = "ANTHROPIC_API_KEY",
    ) -> None:
        self.call_log = call_log or CallLog(None)
        self.timeout = timeout
        self.api_key_env = api_key_env
        self._client: Any | None = None

    def _sdk(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise LLMError(
                "the anthropic package is not installed. Run `uv sync --extra anthropic`, "
                "or drop the `anthropic:` prefix to reach Claude through Venice instead."
            ) from exc
        # A bare client also resolves an `ant auth login` profile, so only complain
        # when neither an env var nor a profile is available.
        key = os.environ.get(self.api_key_env, "").strip()
        try:
            self._client = (
                anthropic.AsyncAnthropic(api_key=key, timeout=self.timeout)
                if key
                else anthropic.AsyncAnthropic(timeout=self.timeout)
            )
        except Exception as exc:
            raise MissingAPIKey(
                f"no Anthropic credentials: set {self.api_key_env} or run `ant auth login` ({exc})"
            ) from exc
        return self._client

    async def aclose(self) -> None:
        client = self._client
        self._client = None
        if client is not None:
            try:
                await client.close()
            except Exception:  # pragma: no cover - closing must never raise
                log.debug("error closing the anthropic client", exc_info=True)

    async def list_models(self) -> dict[str, Any]:
        """Shaped like the Venice payload so `check` can treat both the same."""
        client = self._sdk()
        page = await client.models.list()
        return {"data": [{"id": m.id, "display_name": getattr(m, "display_name", m.id)} for m in page.data]}

    async def complete_json(
        self,
        kind: str,
        messages: list[dict[str, str]],
        *,
        model: str,
        effort: str = "low",
        max_tokens: int = 1500,
        temperature: float = 0.2,
        cache_key: str | None = None,
        timeout: float | None = None,
        web_search: bool = False,
    ) -> tuple[BaseModel, CallUsage]:
        """One call returning a validated model. Retries once on a schema failure."""
        if kind not in SCHEMAS:
            raise LLMError(f"unknown schema kind {kind!r}")
        _, model_cls = SCHEMAS[kind]

        system, turns = _split_system(messages)
        # `temperature` is rejected on Sonnet 5 and the rest of the 4.6+ family, so
        # output shape is steered by the schema and the prompt instead.
        _ = temperature
        request: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": turns,
            "output_config": {"format": {"type": "json_schema", "schema": schema_for(kind, "anthropic")}},
        }
        if system:
            # One cache breakpoint at the end of the system prompt: it holds the
            # outline block, which is byte-identical for a whole session.
            request["system"] = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
        if effort == "none":
            request["thinking"] = {"type": "disabled"}
        else:
            request["thinking"] = {"type": "adaptive"}
            request["output_config"]["effort"] = _EFFORTS.get(effort, "high")
        if web_search:
            request["tools"] = [{"type": "web_search_20260209", "name": "web_search"}]
        _ = cache_key  # caching is prefix-based here, not keyed

        last_error: str | None = None
        for attempt in (0, 1):
            attempt_request = dict(request)
            if attempt == 1 and last_error:
                attempt_request["messages"] = turns + [
                    {
                        "role": "user",
                        "content": (
                            "Your previous answer did not validate against the schema:\n"
                            f"{last_error}\nReturn corrected JSON only."
                        ),
                    }
                ]
            started = time.monotonic()
            try:
                message = await self._send(attempt_request, max_tokens, timeout)
            except LLMError:
                raise
            except Exception as exc:
                latency = time.monotonic() - started
                self._log(kind, attempt_request, None, None, latency, error=repr(exc))
                raise LLMError(f"{type(exc).__name__}: {exc}") from exc
            latency = time.monotonic() - started

            usage = _usage_of(message, model, latency)
            if getattr(message, "stop_reason", None) == "refusal":
                detail = getattr(getattr(message, "stop_details", None), "category", "unknown")
                self._log(kind, attempt_request, _dump(message), usage, latency, error=f"refusal:{detail}")
                raise LLMError(f"Claude declined this request (category {detail})")
            try:
                parsed = model_cls.model_validate_json(_text_of(message))
            except (LLMError, ValidationError, ValueError) as exc:
                last_error = str(exc)[:1200]
                self._log(kind, attempt_request, _dump(message), usage, latency, error=last_error)
                if attempt == 1:
                    raise LLMError(f"invalid JSON from model: {last_error}") from exc
                log.warning("%s: unusable answer (%s), retrying once", kind, last_error[:120])
                continue
            self._log(kind, attempt_request, _dump(message), usage, latency)
            return parsed, usage
        raise LLMError("unreachable")

    async def _send(self, request: dict[str, Any], max_tokens: int, timeout: float | None) -> Any:
        client = self._sdk()
        if timeout is not None:
            client = client.with_options(timeout=timeout)
        if max_tokens > STREAM_ABOVE_TOKENS:
            async with client.messages.stream(**request) as stream:
                return await stream.get_final_message()
        return await client.messages.create(**request)

    def _log(
        self,
        kind: str,
        request: dict[str, Any],
        response: dict[str, Any] | None,
        usage: CallUsage | None,
        latency: float,
        error: str | None = None,
    ) -> None:
        self.call_log.write(
            {
                "ts": time.time(),
                "kind": kind,
                "provider": "anthropic",
                "request": request,
                "response": response,
                "usage": None if usage is None else usage.__dict__,
                "latency_s": round(latency, 3),
                "error": error,
            }
        )


def _split_system(messages: list[dict[str, str]]) -> tuple[str, list[dict[str, str]]]:
    """The Messages API takes the system prompt out of band."""
    system_parts = [m["content"] for m in messages if m.get("role") == "system"]
    turns = [m for m in messages if m.get("role") != "system"]
    if not turns:
        turns = [{"role": "user", "content": "Proceed."}]
    return "\n\n".join(system_parts), turns


def _text_of(message: Any) -> str:
    parts = [b.text for b in getattr(message, "content", []) if getattr(b, "type", None) == "text"]
    text = "".join(parts).strip()
    if not text:
        raise LLMError(f"empty content in response (stop_reason={getattr(message, 'stop_reason', None)})")
    return text


def _usage_of(message: Any, model: str, latency: float) -> CallUsage:
    u = getattr(message, "usage", None)
    fresh = int(getattr(u, "input_tokens", 0) or 0)
    cached = int(getattr(u, "cache_read_input_tokens", 0) or 0)
    written = int(getattr(u, "cache_creation_input_tokens", 0) or 0)
    completion = int(getattr(u, "output_tokens", 0) or 0)
    # `prompt_tokens` is every input token however it was billed, matching what the
    # OpenAI-compatible path reports, so the usage panel stays comparable.
    prompt = fresh + cached + written
    return CallUsage(
        model=model,
        prompt_tokens=prompt,
        cached_tokens=cached,
        completion_tokens=completion,
        cost_usd=estimate_cost(model, prompt, cached, completion),
        latency_s=latency,
        extra={"cache_creation_input_tokens": written},
    )


def _dump(message: Any) -> dict[str, Any]:
    try:
        return json.loads(message.to_json())
    except Exception:  # pragma: no cover - defensive
        return {"repr": repr(message)[:2000]}
