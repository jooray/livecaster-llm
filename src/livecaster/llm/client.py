"""Venice (OpenAI-compatible) client with strict-JSON responses and call logging."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

from livecaster.llm.pricing import estimate_cost
from livecaster.llm.schemas import SCHEMAS, response_format
from livecaster.log import get_logger

log = get_logger(__name__)

VENICE_PARAMETERS = {
    "include_venice_system_prompt": False,
    "strip_thinking_response": True,
    "enable_web_search": "off",
}


class LLMError(RuntimeError):
    pass


class MissingAPIKey(LLMError):
    pass


@dataclass
class CallUsage:
    model: str = ""
    prompt_tokens: int = 0
    cached_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    latency_s: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)


def api_key() -> str:
    key = os.environ.get("VENICE_API_KEY", "").strip()
    if not key:
        raise MissingAPIKey(
            "VENICE_API_KEY is not set. Export it or put it in a .env file in the project root."
        )
    return key


class CallLog:
    """Append-only ``llm.jsonl`` writer (SPEC §8)."""

    def __init__(self, path: Path | str | None) -> None:
        self.path = Path(path) if path else None
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, record: dict[str, Any]) -> None:
        if not self.path:
            return
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
            fh.flush()


class LLMClient:
    """Thin async wrapper over Venice chat completions.

    Kept on ``httpx`` rather than the ``openai`` SDK so the Venice-specific fields
    (``venice_parameters``, ``prompt_cache_key``) travel verbatim and every raw
    response can be written to ``llm.jsonl``.
    """

    def __init__(
        self,
        base_url: str = "https://api.venice.ai/api/v1",
        *,
        call_log: CallLog | None = None,
        timeout: float = 30.0,
        key: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.call_log = call_log or CallLog(None)
        self.timeout = timeout
        self._key = key
        self._client: httpx.AsyncClient | None = None

    @property
    def key(self) -> str:
        if self._key is None:
            self._key = api_key()
        return self._key

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"},
                timeout=httpx.Timeout(self.timeout),
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def list_models(self) -> dict[str, Any]:
        client = await self._http()
        resp = await client.get("/models")
        resp.raise_for_status()
        return resp.json()

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

        venice_params = dict(VENICE_PARAMETERS)
        if web_search:
            venice_params["enable_web_search"] = "on"
            venice_params["enable_web_citations"] = True

        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "response_format": response_format(kind),  # type: ignore[arg-type]
            "temperature": temperature,
            "max_completion_tokens": max_tokens,
            "venice_parameters": venice_params,
        }
        # "none" must be sent explicitly: omitting the field lets the model reason
        # freely, which on DeepSeek V4 Flash can swallow the entire token budget.
        if effort:
            body["reasoning_effort"] = effort
        if cache_key:
            body["prompt_cache_key"] = cache_key

        last_error: str | None = None
        for attempt in (0, 1):
            attempt_body = json.loads(json.dumps(body))
            if attempt == 1 and last_error:
                attempt_body["messages"] = list(messages) + [
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
                client = await self._http()
                resp = await client.post(
                    "/chat/completions",
                    json=attempt_body,
                    timeout=httpx.Timeout(timeout or self.timeout),
                )
                latency = time.monotonic() - started
                if resp.status_code >= 400:
                    detail = resp.text[:800]
                    self._log_call(kind, attempt_body, None, None, latency, error=detail)
                    raise LLMError(f"Venice returned {resp.status_code}: {detail}")
                payload = resp.json()
            except LLMError:
                raise
            except Exception as exc:  # network, timeout, bad JSON envelope
                latency = time.monotonic() - started
                self._log_call(kind, attempt_body, None, None, latency, error=repr(exc))
                raise LLMError(f"{type(exc).__name__}: {exc}") from exc

            usage = self._usage_from(payload, model, latency)
            content = _content_of(payload)
            try:
                parsed = model_cls.model_validate_json(content)
            except (ValidationError, ValueError) as exc:
                last_error = str(exc)[:1200]
                self._log_call(kind, attempt_body, payload, usage, latency, error=last_error)
                if attempt == 1:
                    raise LLMError(f"invalid JSON from model: {last_error}") from exc
                log.warning("%s: schema validation failed, retrying once", kind)
                continue
            self._log_call(kind, attempt_body, payload, usage, latency)
            return parsed, usage
        raise LLMError("unreachable")

    def _usage_from(self, payload: dict[str, Any], model: str, latency: float) -> CallUsage:
        u = payload.get("usage") or {}
        prompt = int(u.get("prompt_tokens") or 0)
        completion = int(u.get("completion_tokens") or 0)
        details = u.get("prompt_tokens_details") or {}
        cached = int(details.get("cached_tokens") or 0)
        return CallUsage(
            model=model,
            prompt_tokens=prompt,
            cached_tokens=cached,
            completion_tokens=completion,
            cost_usd=estimate_cost(model, prompt, cached, completion),
            latency_s=latency,
        )

    def _log_call(
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
                "request": request,
                "response": response,
                "usage": None if usage is None else usage.__dict__,
                "latency_s": round(latency, 3),
                "error": error,
            }
        )


def _content_of(payload: dict[str, Any]) -> str:
    try:
        choice = payload["choices"][0]
    except (KeyError, IndexError) as exc:
        raise LLMError(f"no choices in response: {str(payload)[:300]}") from exc
    message = choice.get("message") or {}
    content = message.get("content")
    if isinstance(content, list):  # some providers return content parts
        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
    if not content:
        raise LLMError("empty content in response")
    return content
