"""Model pricing (USD per 1M tokens) with an optional refresh from Venice /models."""

from __future__ import annotations

from dataclasses import dataclass

from livecaster.log import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class Price:
    """USD per 1M tokens."""

    input: float
    cached_input: float
    output: float


# Venice, 2026-09-03 (SPEC §11).
PRICING: dict[str, Price] = {
    "deepseek-v4-flash-0731": Price(0.175, 0.035, 0.35),
    "deepseek-v4-flash-0731-fast": Price(0.35, 0.0875, 0.70),
    "deepseek-v4-pro-0813": Price(1.65, 0.165, 4.95),
    "e2ee-deepseek-v4-flash": Price(0.21, 0.042, 0.42),
    # Claude through Venice — Venice's own rates, not Anthropic's list prices.
    "claude-sonnet-5": Price(3.0, 0.30, 15.0),
    "claude-opus-5": Price(6.0, 0.60, 30.0),
    "claude-fable-5-1": Price(12.0, 0.30, 60.0),
    "openai-gpt-56-terra": Price(3.125, 0.3125, 18.75),
}

_DEFAULT = Price(0.175, 0.035, 0.35)


def price_for(model: str) -> Price:
    return PRICING.get(model, _DEFAULT)


def estimate_cost(model: str, prompt_tokens: int, cached_tokens: int, completion_tokens: int) -> float:
    """Cost in USD. Cached tokens are billed at the cached rate, not the full rate."""
    p = price_for(model)
    fresh = max(0, prompt_tokens - cached_tokens)
    return (fresh * p.input + cached_tokens * p.cached_input + completion_tokens * p.output) / 1_000_000.0


def _first_float(d: dict, *keys: str) -> float | None:
    for k in keys:
        v = d.get(k)
        if isinstance(v, int | float):
            return float(v)
    return None


def refresh_from_models(models_payload: dict) -> list[str]:
    """Update PRICING from a Venice ``GET /models`` payload. Returns updated model ids.

    Venice reports prices per 1M tokens under ``model_spec.pricing``; the exact key
    names have moved around, so accept a few spellings and ignore anything else.
    """
    updated: list[str] = []
    for item in models_payload.get("data", []) or []:
        model_id = item.get("id")
        if not model_id:
            continue
        spec = item.get("model_spec") or {}
        pricing = spec.get("pricing") or item.get("pricing") or {}
        if not isinstance(pricing, dict):
            continue
        inp = pricing.get("input")
        out = pricing.get("output")
        inp_v = _first_float(inp, "usd", "usd_per_1m", "price") if isinstance(inp, dict) else None
        out_v = _first_float(out, "usd", "usd_per_1m", "price") if isinstance(out, dict) else None
        if inp_v is None:
            inp_v = _first_float(pricing, "input", "input_usd_per_1m")
        if out_v is None:
            out_v = _first_float(pricing, "output", "output_usd_per_1m")
        cached_v = None
        if isinstance(inp, dict):
            cached_v = _first_float(inp, "cached_usd", "cached", "cached_usd_per_1m")
        if cached_v is None:
            cached_v = _first_float(pricing, "input_cached", "cached_input")
        if inp_v is None or out_v is None:
            continue
        PRICING[model_id] = Price(inp_v, cached_v if cached_v is not None else inp_v * 0.2, out_v)
        updated.append(model_id)
    if updated:
        log.debug("refreshed pricing for %d models", len(updated))
    return updated
