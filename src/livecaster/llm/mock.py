"""Offline LLM: canned responses from a fixtures directory (FR-32)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from livecaster.llm.client import CallLog, CallUsage, LLMError
from livecaster.llm.schemas import SCHEMAS
from livecaster.log import get_logger

log = get_logger(__name__)

DEFAULT_FIXTURES = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "tick_responses"


class MockLLM:
    """Serves ``tick`` responses from a directory in filename order, looping.

    Files that do not parse or do not validate raise ``LLMError`` exactly like the
    real client, so the reasoner's error path gets exercised offline too.
    """

    def __init__(
        self,
        fixtures_dir: str | Path | None = None,
        *,
        call_log: CallLog | None = None,
        latency_s: float = 0.0,
    ) -> None:
        self.dir = Path(fixtures_dir) if fixtures_dir else DEFAULT_FIXTURES
        self.call_log = call_log or CallLog(None)
        self.latency_s = latency_s
        self._files = sorted(p for p in self.dir.glob("*.json")) if self.dir.is_dir() else []
        self._i = 0
        self.base_url = "mock://"

    async def aclose(self) -> None:
        return None

    async def list_models(self) -> dict[str, Any]:
        return {"data": []}

    def _next_file(self) -> Path | None:
        if not self._files:
            return None
        p = self._files[self._i % len(self._files)]
        self._i += 1
        return p

    #: Fixture file names for the non-tick call kinds, relative to the fixtures root.
    ALIASES = {"final": "final_analysis.json", "preflight": "preflight.json", "links": "links.json"}

    def _fixture_for(self, kind: str) -> Path | None:
        if kind == "tick":
            return self._next_file()
        root = self.dir.parent
        for name in (self.ALIASES.get(kind, f"{kind}.json"), f"{kind}.json"):
            candidate = root / name
            if candidate.is_file():
                return candidate
        return None

    async def complete_json(
        self,
        kind: str,
        messages: list[dict[str, str]],
        *,
        model: str = "mock",
        effort: str = "low",
        max_tokens: int = 1500,
        temperature: float = 0.2,
        cache_key: str | None = None,
        timeout: float | None = None,
        web_search: bool = False,
    ) -> tuple[BaseModel, CallUsage]:
        _, model_cls = SCHEMAS[kind]
        path = self._fixture_for(kind)
        prompt_chars = sum(len(m["content"]) for m in messages)
        usage = CallUsage(
            model=f"mock:{model}",
            prompt_tokens=int(prompt_chars / 3.2),
            cached_tokens=0,
            completion_tokens=0,
            cost_usd=0.0,
            latency_s=self.latency_s,
        )
        if path is None:
            parsed = model_cls()
            self._log(kind, messages, None, usage, str(path))
            return parsed, usage

        raw = path.read_text(encoding="utf-8")
        usage.completion_tokens = int(len(raw) / 3.2)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            self._log(kind, messages, None, usage, str(path), error=str(exc))
            raise LLMError(f"mock fixture {path.name} is not valid JSON: {exc}") from exc
        try:
            parsed = model_cls.model_validate(data)
        except ValidationError as exc:
            self._log(kind, messages, data, usage, str(path), error=str(exc))
            raise LLMError(f"mock fixture {path.name} failed validation: {exc}") from exc
        self._log(kind, messages, data, usage, str(path))
        return parsed, usage

    def _log(
        self,
        kind: str,
        messages: list[dict[str, str]],
        response: Any,
        usage: CallUsage,
        fixture: str,
        error: str | None = None,
    ) -> None:
        self.call_log.write(
            {
                "ts": time.time(),
                "kind": kind,
                "fixture": fixture,
                "request": {"messages": messages, "model": "mock"},
                "response": response,
                "usage": usage.__dict__,
                "latency_s": usage.latency_s,
                "error": error,
            }
        )
