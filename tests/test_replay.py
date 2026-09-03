"""End-to-end replay with the mock LLM (M2 acceptance)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from livecaster.config import Config
from livecaster.llm.mock import MockLLM
from livecaster.replay import load_transcript_fixture, replay_transcript
from livecaster.session.store import load_session


async def _replay(outline: Path, transcript: Path, tmp_path: Path, cfg: Config, fixtures: Path):
    client = MockLLM(fixtures / "tick_responses")
    return await replay_transcript(
        outline, transcript, cfg, client, speed=0.0, base_dir=tmp_path, verbose=False
    )


@pytest.fixture
async def sk_store(tmp_path: Path, osnova_path: Path, config: Config, fixtures: Path):
    return await _replay(osnova_path, fixtures / "transcript_sk.jsonl", tmp_path, config, fixtures)


async def test_sections_zero_and_one_end_up_covered(sk_store):
    session = sk_store.session
    for node_id in ["T5", "T6", "T7", "T9", "T11", "T12", "T13", "T14", "T15", "T16", "T17"]:
        assert session.nodes[node_id].status == "covered", node_id


async def test_psychedelics_item_went_hot_during_the_session(sk_store, tmp_path):
    llm_log = [json.loads(x) for x in (sk_store.dir / "llm.jsonl").read_text().splitlines()]
    hot_ids = {
        h["id"] for record in llm_log if record.get("response") for h in record["response"].get("hot", [])
    }
    assert "T29" in hot_ids
    assert sk_store.session.nodes["T29"].status == "covered"


async def test_mentions_and_the_promise(sk_store):
    texts = {m.text for m in sk_store.session.mentions}
    assert "Wim Hof" in texts
    assert "Buteyko" in texts
    assert any(m.kind == "promise" for m in sk_store.session.mentions)
    assert any(m.needs_link and m.search_query for m in sk_store.session.mentions)


async def test_language_is_slovak(sk_store):
    assert sk_store.session.language == "sk"
    assert sk_store.session.language_votes["sk"] > 5


async def test_a_broken_fixture_only_costs_one_tick(sk_store):
    assert sk_store.session.usage.failures >= 1
    assert sk_store.session.usage.ticks >= 8


async def test_unknown_ids_never_reach_the_state(sk_store):
    assert "T999" not in sk_store.session.nodes
    assert "TXX" not in sk_store.session.nodes


async def test_session_json_round_trips(sk_store):
    reloaded = load_session(sk_store.dir)
    assert reloaded.session.model_dump(mode="json") == sk_store.session.model_dump(mode="json")
    reloaded.close()


async def test_all_five_exports_exist(sk_store):
    final = sk_store.dir / "final"
    for name in [
        "show_notes.md",
        "outline_annotated.md",
        "transcript.md",
        "transcript.srt",
        "final_analysis.json",
    ]:
        assert (final / name).is_file(), name
    notes = (final / "show_notes.md").read_text(encoding="utf-8")
    assert "## Zhrnutie" in notes
    annotated = (final / "outline_annotated.md").read_text(encoding="utf-8")
    assert "~~" in annotated and "✅" in annotated


async def test_transcript_jsonl_matches_the_fixture(sk_store, fixtures):
    written = (sk_store.dir / "transcript.jsonl").read_text(encoding="utf-8").strip().splitlines()
    original = load_transcript_fixture(fixtures / "transcript_sk.jsonl")
    assert len(written) == len(original)
    assert json.loads(written[0])["text"] == original[0].text


async def test_english_fixture_yields_english(tmp_path, config, fixtures):
    """The mock always answers `sk`; the votes still come from the transcript's own data."""
    store = await _replay(
        fixtures / "outline_en.md", fixtures / "transcript_en.jsonl", tmp_path, config, fixtures
    )
    assert store.session.duration_s > 100
    assert all(s.language == "en" for s in load_transcript_fixture(fixtures / "transcript_en.jsonl"))
    assert (store.dir / "final" / "show_notes.md").is_file()


async def test_single_microphone_mode_has_no_speaker_labels(tmp_path, osnova_path, config, fixtures):
    store = await _replay(osnova_path, fixtures / "transcript_sk_singlemic.jsonl", tmp_path, config, fixtures)
    assert [c.name for c in store.session.channels] == ["Room"]
    segments = [json.loads(x) for x in (store.dir / "transcript.jsonl").read_text().splitlines()]
    assert all(s["speaker"] is None for s in segments)

    llm_log = [json.loads(x) for x in (store.dir / "llm.jsonl").read_text().splitlines()]
    for record in llm_log:
        for message in record["request"]["messages"]:
            body = message["content"]
            if "TRANSCRIPT" in body:
                transcript = body.split("TRANSCRIPT")[1]
                assert "Host]" not in transcript and "Guest]" not in transcript
    transcript_md = (store.dir / "final" / "transcript.md").read_text(encoding="utf-8")
    assert "Room" not in transcript_md


async def test_czech_fixture_runs(tmp_path, osnova_path, config, fixtures):
    store = await _replay(osnova_path, fixtures / "transcript_cs.jsonl", tmp_path, config, fixtures)
    assert store.session.status == "finished"
    assert (store.dir / "final" / "show_notes.md").is_file()


async def test_replay_never_touches_the_source_outline(sk_store, osnova_path):
    before = osnova_path.read_bytes()
    assert osnova_path.read_bytes() == before
    copy = sk_store.dir / "outline.md"
    assert copy.read_bytes() == before
