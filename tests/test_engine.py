"""Engine wiring: audio pipeline, controls, outline reload, resume (M4, M5, M7)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from livecaster.config import ChannelConfig, Config
from livecaster.engine import ChannelPipeline, Engine
from livecaster.llm.mock import MockLLM
from livecaster.session.models import Segment
from livecaster.session.store import create_session, load_session
from livecaster.session.transcript import Transcript
from livecaster.stt.base import STTResult
from livecaster.timeutil import ManualClock, SessionClock


def write_wav(path: Path, seconds: float = 3.0, rate: int = 16_000) -> Path:
    t = np.arange(int(seconds * rate)) / rate
    tone = 0.4 * np.sin(2 * np.pi * 220 * t)
    silence = np.zeros(int(0.8 * rate))
    audio = np.concatenate([silence, tone, silence]).astype(np.float32)
    sf.write(str(path), audio, rate)
    return path


@pytest.fixture
def engine(store, config: Config, fixtures: Path):
    return Engine(store, config, MockLLM(fixtures / "tick_responses"), clock=ManualClock())


async def test_startup_and_shutdown_are_clean(engine: Engine):
    await engine.startup()
    assert engine.loop is not None
    await engine.shutdown()
    assert engine._tasks == []


async def test_add_segment_warms_the_fast_lane(engine: Engine, fixtures: Path):
    from livecaster.llm.schemas import PreflightResult
    from livecaster.session.reducer import apply_preflight

    result = PreflightResult.model_validate(json.loads((fixtures / "preflight.json").read_text()))
    apply_preflight(engine.store.session, result, engine.store.outline)
    engine.fastlane.rebuild(engine.store.outline, engine.store.session.preflight)

    await engine.startup()
    engine.add_segment(
        Segment(
            id="S1",
            channel="Guest",
            speaker="Guest",
            t0=0,
            t1=3,
            text="hovorili sme o ayahuaske a psychedelikach",
        )
    )
    warm = {i: s.warm for i, s in engine.store.session.nodes.items() if s.warm > 0}
    assert warm, "at least one node should be warm"
    assert all(s.status == "untouched" for s in engine.store.session.nodes.values())
    await engine.shutdown()


async def test_crosstalk_drop_is_not_persisted(engine: Engine):
    await engine.startup()
    a = Segment(id="S1", channel="Host", speaker="Host", t0=10, t1=13, text="Dych je veľmi dôležitý")
    b = Segment(id="S2", channel="Guest", speaker="Guest", t0=10.1, t1=13.1, text="Dych je veľmi dôležitý")
    engine.add_segment(a)
    engine.add_segment(b)
    lines = engine.store.transcript_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2  # both were written before the dedupe decision
    assert [s.id for s in engine.transcript.segments] == ["S2"]
    await engine.shutdown()


async def test_sync_mark(engine: Engine):
    await engine.startup()
    engine.clock.set(42.0)
    assert engine.sync_mark() == 42.0
    assert engine.store.session.sync_marks == [42.0]
    events = [json.loads(x) for x in engine.store.dir.joinpath("events.jsonl").read_text().splitlines()]
    assert any(e["kind"] == "sync_mark" and e["t"] == 42.0 for e in events)
    await engine.shutdown()


async def test_pause_and_resume_change_status(engine: Engine):
    await engine.startup()
    engine.store.session.status = "running"
    await engine.pause()
    assert engine.store.session.status == "paused"
    await engine.resume()
    assert engine.store.session.status == "running"
    await engine.shutdown()


async def test_finish_writes_the_exports(engine: Engine):
    await engine.startup()
    engine.store.session.status = "running"
    engine.add_segment(
        Segment(id="S1", channel="Host", speaker="Host", t0=0, t1=4, text="Dnes hovoríme o dychu")
    )
    paths = await engine.finish()
    assert engine.store.session.status == "finished"
    assert Path(paths["show_notes"]).is_file()
    await engine.shutdown()


async def test_finish_is_idempotent(engine: Engine):
    await engine.startup()
    engine.add_segment(Segment(id="S1", channel="Host", t0=0, t1=4, text="ahoj"))
    first = await engine.finish()
    second = await engine.finish()
    assert first == second
    await engine.shutdown()


# --- outline reload (FR-04) ----------------------------------------------


async def test_reload_outline_keeps_states_and_adds_the_new_node(
    tmp_path: Path, osnova_path: Path, config: Config, fixtures: Path
):
    working = tmp_path / "osnova.md"
    working.write_text(osnova_path.read_text(encoding="utf-8"), encoding="utf-8")
    store = create_session(working, config, mode="replay", base_dir=tmp_path / "sessions")
    engine = Engine(store, config, MockLLM(fixtures / "tick_responses"), clock=ManualClock())
    await engine.startup()

    engine.manual_cover = None
    from livecaster.session.reducer import ManualAction

    engine.manual(ManualAction(kind="mark", node_id="T5", status="covered"))
    engine.manual(ManualAction(kind="mark", node_id="T15", status="skipped"))

    text = working.read_text(encoding="utf-8")
    lines = text.split("\n")
    i = next(n for n, line in enumerate(lines) if line.startswith("- CO2 tolerancia"))
    lines.insert(i, "- Nový bod pridaný počas nahrávania")
    working.write_text("\n".join(lines), encoding="utf-8")

    assert engine.reload_outline() is True
    session = engine.store.session
    assert session.nodes["T5"].status == "covered"
    assert session.nodes["T15"].status == "skipped"
    assert session.retired_nodes == {}
    assert store.outline.get("T15").text.startswith("CO2 tolerancia")
    assert store.outline.get("T53").text.startswith("Nový bod")
    assert (store.dir / "outline.1.md").is_file()
    await engine.shutdown()


async def test_reload_outline_retires_a_deleted_node(
    tmp_path: Path, osnova_path: Path, config: Config, fixtures: Path
):
    working = tmp_path / "osnova.md"
    working.write_text(osnova_path.read_text(encoding="utf-8"), encoding="utf-8")
    store = create_session(working, config, mode="replay", base_dir=tmp_path / "sessions")
    engine = Engine(store, config, MockLLM(fixtures / "tick_responses"), clock=ManualClock())
    await engine.startup()

    from livecaster.session.reducer import ManualAction

    engine.manual(ManualAction(kind="mark", node_id="T15", status="covered"))
    text = working.read_text(encoding="utf-8")
    working.write_text(
        "\n".join(line for line in text.split("\n") if not line.startswith("- CO2 tolerancia")),
        encoding="utf-8",
    )
    assert engine.reload_outline() is True
    assert "T15" in engine.store.session.retired_nodes
    assert "T15" not in engine.store.session.nodes
    await engine.shutdown()


async def test_reload_outline_survives_a_missing_file(engine: Engine, tmp_path: Path):
    await engine.startup()
    engine.store.session.outline_path = str(tmp_path / "gone.md")
    assert engine.reload_outline() is False
    await engine.shutdown()


# --- audio pipeline -------------------------------------------------------


def test_channel_pipeline_transcribes_a_wav(tmp_path: Path, config: Config):
    wav = write_wav(tmp_path / "tone.wav", seconds=2.0)
    utterances = []
    pipeline = ChannelPipeline(
        ChannelConfig(name="Host", source=f"file:{wav}", record=False),
        config,
        lambda: 0.0,
        utterances.append,
        None,
        speed=0.0,
    )
    pipeline.start()
    pipeline.source.join(20)
    pipeline.stop()
    assert utterances, "the tone burst should produce one utterance"
    assert utterances[0].channel == "Host"
    assert utterances[0].duration > 1.0


def test_channel_pipeline_records_a_wav(tmp_path: Path, config: Config):
    from livecaster.audio.recorder import WavRecorder

    wav = write_wav(tmp_path / "tone.wav", seconds=1.5)
    recorder = WavRecorder(tmp_path / "out.wav")
    recorder.open()
    pipeline = ChannelPipeline(
        ChannelConfig(name="Host", source=f"file:{wav}", record=True),
        config,
        lambda: 0.0,
        lambda u: None,
        recorder,
        speed=0.0,
    )
    pipeline.start()
    pipeline.source.join(20)
    pipeline.stop()
    audio, rate = sf.read(str(tmp_path / "out.wav"))
    assert rate == 16_000
    assert len(audio) > 16_000
    assert float(np.sqrt(np.mean(np.square(audio)))) > 0.05
    assert recorder.seconds > 1.0


async def test_engine_runs_a_wav_through_a_mock_engine(
    tmp_path: Path, osnova_path: Path, config: Config, fixtures: Path, monkeypatch
):
    from livecaster.stt.mock import MockEngine

    wav = write_wav(tmp_path / "tone.wav", seconds=2.0)
    config.audio.channels = [ChannelConfig(name="Host", source=f"file:{wav}", record=False)]
    config.audio.record = False
    config.stt.engine = "mock"
    store = create_session(osnova_path, config, mode="replay", base_dir=tmp_path / "sessions")

    monkeypatch.setattr(
        "livecaster.engine.select_engine",
        lambda *a, **k: MockEngine(["Predĺžený výdych aktivuje parasympatikus"]),
    )
    done = asyncio.Event()
    engine = Engine(
        store,
        config,
        MockLLM(fixtures / "tick_responses"),
        replay_speed=0.0,
        autostart=True,
        on_replay_finished=done.set,
    )
    await engine.startup()
    await asyncio.wait_for(done.wait(), timeout=20)
    await asyncio.sleep(0.5)
    await engine.finish()
    assert engine.transcript.segments
    assert engine.transcript.segments[0].text.startswith("Predĺžený")
    assert engine.transcript.segments[0].speaker is None  # single channel
    assert engine.transcript.segments[0].engine == "mock"
    await engine.shutdown()


# --- resume (FR-28) -------------------------------------------------------


async def test_resume_reloads_the_transcript_and_continues_the_clock(
    tmp_path: Path, osnova_path: Path, config: Config, fixtures: Path
):
    store = create_session(osnova_path, config, mode="replay", base_dir=tmp_path)
    engine = Engine(store, config, MockLLM(fixtures / "tick_responses"), clock=ManualClock())
    await engine.startup()
    for i in range(5):
        engine.add_segment(
            Segment(
                id=engine.transcript.next_id(), channel="Host", t0=i * 10.0, t1=i * 10 + 8, text=f"veta {i}"
            )
        )
    engine.store.session.status = "running"
    engine.store.snapshot(force=True)
    await engine.shutdown()

    reloaded = load_session(store.dir, config)
    transcript = Transcript.load_jsonl(reloaded.transcript_path)
    assert len(transcript.segments) == 5
    assert transcript.next_id() == "S6"
    clock = SessionClock(offset=transcript.duration())
    assert clock.now() >= 48.0
    assert reloaded.session.nodes == store.session.nodes
    reloaded.close()


async def test_stt_result_marshalling(engine: Engine, monkeypatch):
    from livecaster.audio.segmenter import Utterance

    await engine.startup()

    class Dummy:
        name = "dummy"

    class FakeWorker:
        engine = Dummy()

        def stop(self, drain: bool = True) -> None:
            return None

    engine.stt = FakeWorker()
    utterance = Utterance(channel="Host", t0=1.0, t1=3.0, audio=np.zeros(16, dtype=np.float32))
    engine._ingest(utterance, STTResult(text="ahoj svet", language="sk"))
    assert engine.transcript.segments[-1].text == "ahoj svet"
    assert engine.transcript.segments[-1].speaker == "Host"  # two channels in this fixture
    assert engine.transcript.segments[-1].engine == "dummy"
    await engine.shutdown()


async def test_outline_watcher_reloads_on_an_edit(
    tmp_path: Path, osnova_path: Path, config: Config, fixtures: Path
):
    """FR-04 end to end: edit the file on disk, the watcher pushes a fresh outline."""
    import threading

    from livecaster.engine import watch_outline

    working = tmp_path / "osnova.md"
    working.write_text(osnova_path.read_text(encoding="utf-8"), encoding="utf-8")
    store = create_session(working, config, mode="replay", base_dir=tmp_path / "sessions")
    engine = Engine(store, config, MockLLM(fixtures / "tick_responses"), clock=ManualClock())
    await engine.startup()

    reloaded = asyncio.Event()
    store.subscribe(lambda kind, payload: reloaded.set() if kind == "state" else None)

    stop = threading.Event()
    watch_outline(engine, stop)
    try:
        await asyncio.sleep(0.4)  # let the watcher settle before touching the file
        working.write_text(
            working.read_text(encoding="utf-8") + "\n- Bod pridaný počas nahrávania\n", encoding="utf-8"
        )
        await asyncio.wait_for(reloaded.wait(), timeout=10)
    finally:
        stop.set()
    assert len(store.outline.leaves()) == 42
    assert any(n.text.startswith("Bod pridaný") for n in store.outline.nodes)
    assert (store.dir / "outline.1.md").is_file()
    await engine.shutdown()


async def test_start_capture_failure_leaves_the_session_idle(
    tmp_path: Path, osnova_path: Path, config: Config, fixtures: Path
):
    config.audio.channels = [ChannelConfig(name="Host", source=f"file:{tmp_path / 'missing.wav'}")]
    config.audio.record = False
    store = create_session(osnova_path, config, mode="live", base_dir=tmp_path / "sessions")
    engine = Engine(store, config, MockLLM(fixtures / "tick_responses"), clock=ManualClock())
    toasts: list[dict] = []
    store.subscribe(lambda kind, payload: toasts.append(payload) if kind == "toast" else None)
    await engine.startup()

    with pytest.raises(FileNotFoundError):
        await engine.start_capture()
    assert store.session.status == "idle"
    assert engine.channels == []
    assert engine.stt is None
    assert any("Cannot start capture" in t["text"] for t in toasts)
    await engine.shutdown()


async def test_duration_tracks_the_clock_while_running(store, config: Config, fixtures: Path):
    """A crash after a long silence must not rewind the resumed clock."""
    clock = ManualClock()
    engine = Engine(store, config, MockLLM(fixtures / "tick_responses"), clock=clock)
    await engine.startup()
    store.session.status = "running"
    engine.add_segment(Segment(id="S1", channel="Host", speaker="Host", t0=0, t1=10, text="ahoj"))
    clock.set(600.0)
    await asyncio.sleep(10.2)
    assert store.session.duration_s >= 600.0
    assert engine.transcript.duration() == 10.0
    await engine.shutdown()


def test_native_recording_writes_the_device_rate_stream(tmp_path: Path, config: Config):
    """`record_native` keeps the untouched stream next to the 16 kHz one."""
    from livecaster.audio.recorder import WavRecorder

    native = WavRecorder(tmp_path / "host-native.wav")
    pipeline = ChannelPipeline(
        ChannelConfig(name="Host", source="file:/dev/null", record=True),
        config,
        lambda: 0.0,
        lambda u: None,
        None,
        native_recorder=native,
    )
    block = np.full(4800, 0.2, dtype=np.float32)
    pipeline._on_native(block, 48_000)
    pipeline._on_native(block, 48_000)
    pipeline._drain_native()
    native.close()
    audio, rate = sf.read(str(tmp_path / "host-native.wav"))
    assert rate == 48_000
    assert len(audio) == 9600
    assert pipeline.dropped_native == 0


# --- a second take (D21) ---------------------------------------------------


async def test_start_after_finish_does_not_capture_twice(
    tmp_path: Path, osnova_path: Path, config: Config, fixtures: Path, monkeypatch
):
    """Finish used to leave the stopped pipelines in place, so the next Start
    restarted them alongside a fresh set and recorded every utterance twice."""
    from livecaster.stt.mock import MockEngine

    wav = write_wav(tmp_path / "tone.wav", seconds=2.0)
    config.audio.channels = [ChannelConfig(name="Room", source=f"file:{wav}", record=False)]
    config.audio.record = False
    config.stt.engine = "mock"
    store = create_session(osnova_path, config, mode="replay", base_dir=tmp_path / "sessions")
    monkeypatch.setattr("livecaster.engine.select_engine", lambda *a, **k: MockEngine(["ahoj"]))

    engine = Engine(store, config, MockLLM(fixtures / "tick_responses"), clock=ManualClock())
    await engine.startup()
    await engine.start_capture()
    assert len(engine.channels) == 1
    first = engine.channels[0]

    await engine.finish()
    assert engine.store.session.status == "finished"

    await engine.start_capture()
    assert len(engine.channels) == 1, "a second take must replace the pipelines, not add to them"
    assert engine.channels[0] is not first
    assert engine.store.session.status == "running"
    # The wrap-up has to be able to run again over the longer session.
    assert engine.finish_result is None
    await engine.shutdown()
    assert engine.channels == []


async def test_shutdown_forgets_the_pipelines(
    tmp_path: Path, osnova_path: Path, config: Config, fixtures: Path, monkeypatch
):
    from livecaster.stt.mock import MockEngine

    wav = write_wav(tmp_path / "tone.wav", seconds=1.0)
    config.audio.channels = [ChannelConfig(name="Room", source=f"file:{wav}", record=False)]
    config.audio.record = False
    config.stt.engine = "mock"
    store = create_session(osnova_path, config, mode="replay", base_dir=tmp_path / "sessions")
    monkeypatch.setattr("livecaster.engine.select_engine", lambda *a, **k: MockEngine(["ahoj"]))

    engine = Engine(store, config, MockLLM(fixtures / "tick_responses"), clock=ManualClock())
    await engine.startup()
    await engine.start_capture()
    await engine.shutdown()
    assert engine.channels == []
    assert engine.stt is None


# --- language (FR-34, D20) -------------------------------------------------


async def test_locking_the_language_stops_the_vote(engine: Engine):
    await engine.startup()
    engine.store.session.language_votes = {"cs": 3, "sk": 1}
    engine.set_language("sk")
    assert engine.config.stt.language == "sk"
    assert engine.store.session.language == "sk"
    assert engine.store.session.language_votes == {"sk": 999}
    await engine.shutdown()


async def test_releasing_the_language_reopens_the_vote(engine: Engine):
    await engine.startup()
    engine.set_language("sk")
    engine.set_language("auto")
    assert engine.config.stt.language == "auto"
    assert engine.store.session.language_votes == {}
    # The last known language stays as the prompt's best guess.
    assert engine.store.session.language == "sk"
    await engine.shutdown()


async def test_the_language_change_reaches_the_running_worker(
    tmp_path: Path, osnova_path: Path, config: Config, fixtures: Path, monkeypatch
):
    from livecaster.stt.mock import MockEngine

    wav = write_wav(tmp_path / "tone.wav", seconds=1.0)
    config.audio.channels = [ChannelConfig(name="Room", source=f"file:{wav}", record=False)]
    config.audio.record = False
    config.stt.engine = "mock"
    store = create_session(osnova_path, config, mode="replay", base_dir=tmp_path / "sessions")
    monkeypatch.setattr("livecaster.engine.select_engine", lambda *a, **k: MockEngine(["ahoj"]))

    engine = Engine(store, config, MockLLM(fixtures / "tick_responses"), clock=ManualClock())
    await engine.startup()
    await engine.start_capture()
    assert engine.stt is not None and engine.stt.language is None
    engine.set_language("cs")
    assert engine.stt.language == "cs"
    await engine.shutdown()


# --- tick tuning (FR-35) ---------------------------------------------------


async def test_ticks_can_be_retuned_live(engine: Engine):
    await engine.startup()
    engine.set_ticks(interval_s=12, min_new_words=10, burst_words=60)
    assert engine.config.llm.tick_interval_s == 12
    assert engine.config.llm.min_new_words == 10
    assert engine.config.llm.burst_words == 60
    # The reasoner reads the config on every pass, so nothing needs restarting.
    assert engine.reasoner.config is engine.config
    assert engine.status_payload()["llm"]["interval_s"] == 12
    await engine.shutdown()
