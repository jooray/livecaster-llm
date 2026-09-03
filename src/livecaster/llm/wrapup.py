"""Wrap-up: final analysis, optional link resolution, exports (FR-29..FR-31, SPEC §7.7)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from livecaster.config import Config
from livecaster.llm.prompts import build_final_messages, build_links_messages
from livecaster.llm.schemas import FinalAnalysis, LinkResolution
from livecaster.log import get_logger
from livecaster.session.exports import write_exports
from livecaster.session.store import SessionStore
from livecaster.session.transcript import Transcript

log = get_logger(__name__)


async def run_final_analysis(
    store: SessionStore,
    transcript: Transcript,
    client: Any,
    config: Config,
    *,
    model: str | None = None,
) -> FinalAnalysis:
    session = store.session
    show_speakers = len(session.channels) > 1 and bool(transcript.speakers())
    messages = build_final_messages(
        session,
        store.outline,
        transcript.segments,
        show_speakers=show_speakers,
        language=session.language,
    )
    result, usage = await client.complete_json(
        "final",
        messages,
        model=model or config.llm.final_model,
        effort=config.llm.reasoning_effort_final,
        max_tokens=config.llm.max_final_tokens,
        temperature=config.llm.temperature,
        cache_key=f"{session.id}-final",
        timeout=config.llm.final_timeout_s,
    )
    assert isinstance(result, FinalAnalysis)
    session.usage.add(
        usage.model, usage.prompt_tokens, usage.cached_tokens, usage.completion_tokens, usage.cost_usd
    )
    if not result.language:
        result.language = session.language or "en"
    return result


async def resolve_links(analysis: FinalAnalysis, client: Any, config: Config) -> int:
    """Fill in URLs for mentions that need one. Only cited URLs are accepted."""
    pending = [m for m in analysis.mentions if m.needs_link and not m.url]
    if not pending:
        return 0
    try:
        result, usage = await client.complete_json(
            "links",
            build_links_messages(pending),
            model=config.llm.final_model,
            effort=config.llm.reasoning_effort_tick,
            max_tokens=2000,
            temperature=0.0,
            timeout=config.llm.final_timeout_s,
            web_search=True,
        )
    except Exception as exc:
        log.warning("link resolution failed: %s", exc)
        return 0
    assert isinstance(result, LinkResolution)
    by_text = {m.text.casefold(): m for m in pending}
    filled = 0
    for link in result.links:
        if not link.url:
            continue
        mention = by_text.get(link.text.casefold())
        if mention is None:
            continue
        mention.url = link.url
        mention.needs_link = False
        filled += 1
    return filled


def _original_outline_text(store: SessionStore) -> str:
    copies = sorted(store.dir.glob("outline.*.md"))
    latest = copies[-1] if copies else store.dir / "outline.md"
    if latest.is_file():
        return latest.read_text(encoding="utf-8")
    source = Path(store.session.outline_path)
    if source.is_file():
        return source.read_text(encoding="utf-8")
    return ""


async def run_wrapup(
    store: SessionStore,
    transcript: Transcript,
    client: Any,
    config: Config,
    *,
    model: str | None = None,
    resolve: bool | None = None,
) -> dict[str, str]:
    """Final analysis + exports. Returns the written paths."""
    session = store.session
    session.duration_s = max(session.duration_s, transcript.duration())
    analysis = await run_final_analysis(store, transcript, client, config, model=model)
    (store.final_dir).mkdir(parents=True, exist_ok=True)
    (store.final_dir / "final_analysis.json").write_text(analysis.model_dump_json(indent=2), encoding="utf-8")
    if resolve if resolve is not None else config.wrapup.resolve_links:
        n = await resolve_links(analysis, client, config)
        log.info("link resolution filled %d urls", n)
    show_speakers = len(session.channels) > 1 and bool(transcript.speakers())
    paths = write_exports(
        store.final_dir,
        analysis,
        session,
        store.outline,
        transcript.segments,
        _original_outline_text(store),
        show_speakers=show_speakers,
    )
    session.final_paths = paths
    store.mark_dirty()
    store.snapshot(force=True)
    store.log_event("wrapup", session.duration_s, paths=paths)
    return paths


def export_only(store: SessionStore, transcript: Transcript) -> dict[str, str]:
    """Re-render the exports from an existing ``final_analysis.json`` (FR-31, `export`)."""
    path = store.final_dir / "final_analysis.json"
    if not path.is_file():
        raise FileNotFoundError(f"{path} not found — run `livecaster wrapup` first")
    analysis = FinalAnalysis.model_validate(json.loads(path.read_text(encoding="utf-8")))
    session = store.session
    show_speakers = len(session.channels) > 1 and bool(transcript.speakers())
    paths = write_exports(
        store.final_dir,
        analysis,
        session,
        store.outline,
        transcript.segments,
        _original_outline_text(store),
        show_speakers=show_speakers,
    )
    session.final_paths = paths
    store.snapshot(force=True)
    return paths
