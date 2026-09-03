"""Optional pre-session pass: questions, trigger phrases and cross-links (FR-05)."""

from __future__ import annotations

from typing import Any

from livecaster.config import Config
from livecaster.llm.prompts import build_preflight_messages
from livecaster.llm.schemas import PreflightResult
from livecaster.log import get_logger
from livecaster.session.models import Preflight
from livecaster.session.reducer import apply_preflight
from livecaster.session.store import SessionStore

log = get_logger(__name__)


async def run_preflight(store: SessionStore, client: Any, config: Config) -> Preflight | None:
    """One call before the session starts. Failure is not fatal."""
    messages = build_preflight_messages(store.outline, store.session.language)
    try:
        result, usage = await client.complete_json(
            "preflight",
            messages,
            model=config.llm.tick_model,
            effort=config.llm.reasoning_effort_tick,
            max_tokens=8000,
            temperature=config.llm.temperature,
            cache_key=store.session.id,
            timeout=config.llm.final_timeout_s,
        )
    except Exception as exc:
        log.warning("pre-flight failed, continuing without it: %s", exc)
        store.log_event("preflight_failed", 0.0, error=str(exc)[:300])
        return None
    assert isinstance(result, PreflightResult)
    store.session.usage.add(
        usage.model, usage.prompt_tokens, usage.cached_tokens, usage.completion_tokens, usage.cost_usd
    )
    pf = apply_preflight(store.session, result, store.outline)
    store.mark_dirty()
    store.log_event("preflight", 0.0, nodes=len(pf.nodes), language=pf.language)
    log.info("pre-flight: %d nodes annotated", len(pf.nodes))
    return pf
