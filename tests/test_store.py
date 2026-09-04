"""Session directory, persistence and the transcript buffer (M2)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from livecaster.config import ChannelConfig, Config
from livecaster.session.models import Mention, NodeState, Segment
from livecaster.session.store import create_session, load_session, slugify
from livecaster.session.transcript import Transcript


def test_create_session_lays_out_the_directory(tmp_path: Path, osnova_path: Path, config: Config):
    store = create_session(osnova_path, config, mode="live", base_dir=tmp_path)
    assert store.dir.parent == tmp_path
    assert store.dir.name.endswith("_osnova")
    assert (store.dir / "session.json").is_file()
    assert (store.dir / "outline.md").read_text(encoding="utf-8") == osnova_path.read_text(encoding="utf-8")
    assert (store.dir / "audio").is_dir()
    assert (store.dir / "final").is_dir()
    assert json.loads((store.dir / "events.jsonl").read_text())["kind"] == "created"
    store.close()


def test_second_session_on_the_same_day_gets_a_suffix(tmp_path: Path, osnova_path: Path, config: Config):
    a = create_session(osnova_path, config, base_dir=tmp_path)
    b = create_session(osnova_path, config, base_dir=tmp_path)
    assert a.dir != b.dir
    assert b.dir.name.endswith("-2")
    a.close()
    b.close()


def test_round_trip_through_disk(store):
    store.session.nodes["T5"] = NodeState(id="T5", status="covered", covered_at=12.0)
    store.session.mentions.append(Mention(id="M1", kind="book", text="Breath"))
    store.session.language = "sk"
    store.snapshot(force=True)

    reloaded = load_session(store.dir)
    assert reloaded.session.nodes["T5"].status == "covered"
    assert reloaded.session.mentions[0].text == "Breath"
    assert reloaded.session.language == "sk"
    assert [n.id for n in reloaded.outline.nodes] == [n.id for n in store.outline.nodes]
    assert reloaded.outline.get("T29") is not None
    store.close()
    reloaded.close()


def test_snapshot_is_debounced_but_forced(store):
    # create_session already wrote one snapshot, so the next second is debounced away.
    store.session.language = "sk"
    store.mark_dirty()
    store.snapshot()
    assert json.loads(store.state_path.read_text())["language"] is None
    store.snapshot(force=True)
    assert json.loads(store.state_path.read_text())["language"] == "sk"

    store.session.language = "cs"
    store.mark_dirty()
    store.snapshot()
    assert json.loads(store.state_path.read_text())["language"] == "sk"
    store._last_snapshot -= 2.0  # pretend a second went by
    store.snapshot()
    assert json.loads(store.state_path.read_text())["language"] == "cs"
    store.close()


def test_snapshot_without_changes_is_a_no_op(store):
    store.snapshot(force=True)
    mtime = store.state_path.stat().st_mtime_ns
    store._last_snapshot -= 2.0
    store.snapshot()
    assert store.state_path.stat().st_mtime_ns == mtime
    store.close()


def test_segments_are_appended_immediately(store):
    store.append_segment(Segment(id="S1", channel="Host", t0=0, t1=1, text="ahoj"))
    store.append_segment(Segment(id="S2", channel="Guest", t0=1, t1=2, text="čau"))
    lines = store.transcript_path.read_text(encoding="utf-8").strip().split("\n")
    assert [json.loads(x)["text"] for x in lines] == ["ahoj", "čau"]
    store.close()


def test_events_are_logged(store):
    store.log_event("sync_mark", 42.0)
    kinds = [json.loads(x)["kind"] for x in store.dir.joinpath("events.jsonl").read_text().splitlines()]
    assert kinds == ["created", "sync_mark"]
    store.close()


@pytest.mark.parametrize(
    "value,expected",
    [("Osnova podcastu", "osnova-podcastu"), ("Podcast o všeličom", "podcast-o-vselicom"), ("", "session")],
)
def test_slugify(value: str, expected: str):
    assert slugify(value) == expected


# --- transcript buffer ----------------------------------------------------


def seg(i: int, channel: str = "Host", text: str = "one two three", t0: float = 0.0) -> Segment:
    return Segment(id=f"S{i}", channel=channel, speaker=channel, t0=t0, t1=t0 + 2, text=text)


def test_window_respects_the_budget_but_keeps_the_new_part():
    t = Transcript()
    for i in range(20):
        t.append(seg(i + 1, t0=i * 3.0))
    window = t.window(12, since_id="S15")
    assert {s.id for s in window} >= {"S16", "S17", "S18", "S19", "S20"}
    assert len(window) < 20


def test_new_since_and_word_counts():
    t = Transcript()
    for i in range(5):
        t.append(seg(i + 1, t0=i * 3.0))
    assert [s.id for s in t.new_since("S3")] == ["S4", "S5"]
    assert t.words_since("S3") == 6
    assert t.words_since(None) == 15
    assert t.total_words() == 15


def test_crosstalk_dedupe_keeps_the_direct_channel():
    t = Transcript(direct_channels={"Guest"})
    mic = Segment(id="S1", channel="Host", speaker="Host", t0=10.0, t1=13.0, text="Dych je veľmi dôležitý")
    direct = Segment(
        id="S2", channel="Guest", speaker="Guest", t0=10.1, t1=13.1, text="Dych je veľmi dôležitý."
    )
    assert t.append(mic) is mic
    assert t.append(direct) is direct
    assert [s.id for s in t.segments] == ["S2"]
    assert t.dropped_crosstalk == 1


def test_crosstalk_dedupe_ignores_different_text_and_no_overlap():
    t = Transcript(direct_channels={"Guest"})
    t.append(Segment(id="S1", channel="Host", t0=0, t1=3, text="Toto je úplne iná veta"))
    t.append(Segment(id="S2", channel="Guest", t0=1, t1=4, text="A toto je celkom iná odpoveď"))
    assert len(t.segments) == 2

    t2 = Transcript(direct_channels={"Guest"})
    t2.append(Segment(id="S1", channel="Host", t0=0, t1=3, text="rovnaká veta tu"))
    t2.append(Segment(id="S2", channel="Guest", t0=5, t1=8, text="rovnaká veta tu"))
    assert len(t2.segments) == 2


def test_crosstalk_dedupe_is_off_without_a_direct_channel():
    t = Transcript()
    t.append(Segment(id="S1", channel="Host", t0=0, t1=3, text="rovnaká veta"))
    t.append(Segment(id="S2", channel="Room", t0=0.1, t1=3.1, text="rovnaká veta"))
    assert len(t.segments) == 2


def test_load_jsonl_round_trip(tmp_path: Path):
    path = tmp_path / "transcript.jsonl"
    path.write_text(
        "\n".join(json.dumps(seg(i + 1, t0=i * 3.0).model_dump()) for i in range(4)) + "\n",
        encoding="utf-8",
    )
    t = Transcript.load_jsonl(path)
    assert [s.id for s in t.segments] == ["S1", "S2", "S3", "S4"]
    assert t.next_id() == "S5"


def test_load_jsonl_skips_broken_lines(tmp_path: Path):
    path = tmp_path / "transcript.jsonl"
    path.write_text(json.dumps(seg(1).model_dump()) + "\nnot json\n\n", encoding="utf-8")
    assert len(Transcript.load_jsonl(path).segments) == 1


def test_channels_are_copied_into_the_session(tmp_path: Path, osnova_path: Path, config: Config):
    config.audio.channels = [
        ChannelConfig(name="Host", source="device:x"),
        ChannelConfig(name="Guest", source="audiotee:Google Chrome", is_direct=True),
    ]
    store = create_session(osnova_path, config, mode="remote", base_dir=tmp_path)
    assert [c.name for c in store.session.channels] == ["Host", "Guest"]
    assert store.session.channels[1].is_direct is True
    assert store.session.mode == "remote"
    store.close()


def test_reloading_a_transcript_repeats_the_crosstalk_dedupe(tmp_path: Path):
    """The file keeps both halves of a duplicate, so a resume must drop one again."""
    from livecaster.session.models import Segment
    from livecaster.session.transcript import Transcript

    path = tmp_path / "transcript.jsonl"
    live = Transcript(direct_channels=["Guest"])
    kept = []
    for seg in (
        Segment(id="S1", channel="Host", speaker="Host", t0=10, t1=13, text="Dych je veľmi dôležitý"),
        Segment(id="S2", channel="Guest", speaker="Guest", t0=10.1, t1=13.1, text="Dych je veľmi dôležitý"),
    ):
        # Every segment reaches the file; only the survivor stays in memory.
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(seg.model_dump(), ensure_ascii=False) + "\n")
        if live.append(seg) is not None:
            kept.append(seg.id)

    assert [s.id for s in live.segments] == ["S2"], "the direct channel wins live"
    assert len(path.read_text(encoding="utf-8").strip().splitlines()) == 2

    reloaded = Transcript.load_jsonl(path, ["Guest"])
    assert [s.id for s in reloaded.segments] == ["S2"]
    assert reloaded.dropped_crosstalk == 1
