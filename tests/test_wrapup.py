"""Wrap-up flow: final analysis, link resolution, export-only (M6)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from livecaster.config import Config
from livecaster.llm.client import CallUsage
from livecaster.llm.mock import MockLLM
from livecaster.llm.schemas import FinalAnalysis, LinkResolution, ResolvedLink, TickMention
from livecaster.llm.wrapup import export_only, resolve_links, run_wrapup
from livecaster.session.models import NodeState, Segment
from livecaster.session.transcript import Transcript


@pytest.fixture
def transcript(fixtures: Path) -> Transcript:
    from livecaster.replay import load_transcript_fixture

    t = Transcript()
    for segment in load_transcript_fixture(fixtures / "transcript_sk.jsonl"):
        t.append(segment)
    return t


async def test_run_wrapup_writes_everything(store, transcript, config: Config, fixtures: Path):
    store.session.nodes["T5"] = NodeState(id="T5", status="covered", covered_at=18.0)
    client = MockLLM(fixtures / "tick_responses")
    paths = await run_wrapup(store, transcript, client, config)
    assert set(paths) == {
        "show_notes",
        "outline_annotated",
        "transcript_md",
        "transcript_srt",
        "final_analysis",
    }
    for path in paths.values():
        assert Path(path).is_file()
    assert store.session.final_paths == paths
    assert store.session.duration_s == pytest.approx(transcript.duration())
    events = [json.loads(x) for x in store.dir.joinpath("events.jsonl").read_text().splitlines()]
    assert any(e["kind"] == "wrapup" for e in events)


async def test_wrapup_prompt_uses_speakers_only_when_there_are_several(store, transcript, config, fixtures):
    class Recorder(MockLLM):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.seen: list[Any] = []

        async def complete_json(self, kind, messages, **kwargs):
            self.seen.append(messages)
            return await super().complete_json(kind, messages, **kwargs)

    client = Recorder(fixtures / "tick_responses")
    await run_wrapup(store, transcript, client, config)
    body = client.seen[0][1]["content"]
    assert "Host]" in body  # two channels in the store fixture

    store.session.channels = store.session.channels[:1]
    client.seen.clear()
    await run_wrapup(store, transcript, client, config)
    assert "Host]" not in client.seen[0][1]["content"].split("FULL TRANSCRIPT")[1]


async def test_export_only_does_not_call_the_llm(store, transcript, config, fixtures):
    client = MockLLM(fixtures / "tick_responses")
    await run_wrapup(store, transcript, client, config)
    (store.final_dir / "show_notes.md").unlink()

    paths = export_only(store, transcript)
    assert Path(paths["show_notes"]).is_file()


def test_export_only_needs_a_final_analysis(store, transcript):
    with pytest.raises(FileNotFoundError):
        export_only(store, transcript)


async def test_resolve_links_only_accepts_returned_urls(config: Config):
    analysis = FinalAnalysis(
        mentions=[
            TickMention(kind="book", text="Breath", context="", url=None, needs_link=True, search_query="q"),
            TickMention(
                kind="person", text="Wim Hof", context="", url=None, needs_link=True, search_query="q"
            ),
            TickMention(
                kind="link",
                text="dychova-praca.example",
                context="",
                url="https://dychova-praca.example",
                needs_link=False,
                search_query=None,
            ),
        ]
    )

    class Client:
        def __init__(self) -> None:
            self.kwargs: dict[str, Any] = {}

        async def complete_json(self, kind, messages, **kwargs):
            self.kwargs = kwargs
            self.messages = messages
            return (
                LinkResolution(
                    links=[
                        ResolvedLink(text="Breath", url="https://www.mrjamesnestor.com/breath"),
                        ResolvedLink(text="Wim Hof", url=None),
                    ]
                ),
                CallUsage(model="m"),
            )

    client = Client()
    filled = await resolve_links(analysis, client, config)
    assert filled == 1
    assert client.kwargs["web_search"] is True
    assert analysis.mentions[0].url.endswith("breath")
    assert analysis.mentions[0].needs_link is False
    assert analysis.mentions[1].url is None and analysis.mentions[1].needs_link is True
    assert "dychova-praca.example" not in client.messages[1]["content"]  # already had a URL


async def test_resolve_links_survives_a_failure(config: Config):
    analysis = FinalAnalysis(
        mentions=[
            TickMention(kind="book", text="Breath", context="", url=None, needs_link=True, search_query="q")
        ]
    )

    class Failing:
        async def complete_json(self, *a, **k):
            raise RuntimeError("network down")

    assert await resolve_links(analysis, Failing(), config) == 0
    assert analysis.mentions[0].needs_link is True


async def test_resolve_links_with_nothing_to_do(config: Config):
    assert await resolve_links(FinalAnalysis(), object(), config) == 0


async def test_wrapup_uses_the_latest_outline_copy(store, transcript, config, fixtures):
    (store.dir / "outline.1.md").write_text("# Zmenená osnova\n\n- jeden bod\n", encoding="utf-8")
    client = MockLLM(fixtures / "tick_responses")
    paths = await run_wrapup(store, transcript, client, config)
    annotated = Path(paths["outline_annotated"]).read_text(encoding="utf-8")
    assert annotated.startswith("# Zmenená osnova")


async def test_wrapup_model_override(store, transcript, config, fixtures):
    class Recorder(MockLLM):
        model_used = None

        async def complete_json(self, kind, messages, **kwargs):
            if kind == "final":
                Recorder.model_used = kwargs.get("model")
            return await super().complete_json(kind, messages, **kwargs)

    await run_wrapup(
        store, transcript, Recorder(fixtures / "tick_responses"), config, model="deepseek-v4-pro-0813"
    )
    assert Recorder.model_used == "deepseek-v4-pro-0813"


async def test_wrapup_without_a_transcript(store, config, fixtures):
    empty = Transcript()
    paths = await run_wrapup(store, empty, MockLLM(fixtures / "tick_responses"), config)
    assert Path(paths["transcript_srt"]).read_text(encoding="utf-8").strip() == ""


async def test_chapters_are_offset_by_the_sync_mark(store, transcript, config, fixtures):
    store.session.sync_marks = [60.0]
    paths = await run_wrapup(store, transcript, MockLLM(fixtures / "tick_responses"), config)
    notes = Path(paths["show_notes"]).read_text(encoding="utf-8")
    assert "`00:00:35` **Fyziológia výdychu**" in notes  # 95 - 60
    srt = Path(paths["transcript_srt"]).read_text(encoding="utf-8")
    assert srt.startswith("1\n00:00:00,000")


async def test_single_segment_session(store, config, fixtures):
    t = Transcript()
    t.append(Segment(id="S1", channel="Host", speaker="Host", t0=0, t1=4, text="Krátka epizóda"))
    paths = await run_wrapup(store, t, MockLLM(fixtures / "tick_responses"), config)
    assert "Krátka epizóda" in Path(paths["transcript_md"]).read_text(encoding="utf-8")
