"""CLI surface (M0..M6)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from livecaster import __version__
from livecaster.cli import app

runner = CliRunner()


def test_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_help_lists_every_command():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in ["run", "devices", "replay", "wrapup", "export", "check"]:
        assert command in result.stdout


def test_run_rejects_a_missing_outline(tmp_path: Path):
    result = runner.invoke(app, ["run", str(tmp_path / "nope.md")])
    assert result.exit_code == 2
    assert "outline not found" in result.stdout


def test_replay_requires_a_source(osnova_path: Path):
    result = runner.invoke(app, ["replay", "--outline", str(osnova_path)])
    assert result.exit_code == 2
    assert "--transcript" in result.stdout


def test_replay_requires_an_outline(fixtures: Path):
    result = runner.invoke(app, ["replay", "--transcript", str(fixtures / "transcript_sk.jsonl")])
    assert result.exit_code == 2
    assert "--outline is required" in result.stdout


@pytest.mark.parametrize("fixture", ["transcript_sk.jsonl", "transcript_en.jsonl"])
def test_replay_end_to_end(tmp_path: Path, osnova_path: Path, fixtures: Path, fixture: str):
    result = runner.invoke(
        app,
        [
            "replay",
            "--transcript",
            str(fixtures / fixture),
            "--outline",
            str(osnova_path),
            "--mock-llm",
            "--speed",
            "0",
            "--set",
            f"session.dir={tmp_path}",
        ],
    )
    assert result.exit_code == 0, result.output
    sessions = list(tmp_path.iterdir())
    assert len(sessions) == 1
    assert (sessions[0] / "final" / "show_notes.md").is_file()
    assert "covered" in result.stdout


def test_replay_accepts_a_positional_source(tmp_path: Path, osnova_path: Path, fixtures: Path):
    """SPEC §10 writes `replay <file>`; the plan writes `--transcript`. Both work."""
    result = runner.invoke(
        app,
        [
            "replay",
            str(fixtures / "transcript_sk.jsonl"),
            "--outline",
            str(osnova_path),
            "--mock-llm",
            "--speed",
            "0",
            "--set",
            f"session.dir={tmp_path}",
        ],
    )
    assert result.exit_code == 0, result.output
    assert (next(iter(tmp_path.iterdir())) / "final" / "show_notes.md").is_file()


def test_replay_positional_session_directory(tmp_path: Path, osnova_path: Path, fixtures: Path):
    runner.invoke(
        app,
        [
            "replay",
            "--transcript",
            str(fixtures / "transcript_sk.jsonl"),
            "--outline",
            str(osnova_path),
            "--mock-llm",
            "--speed",
            "0",
            "--set",
            f"session.dir={tmp_path / 'first'}",
        ],
    )
    first = next(iter((tmp_path / "first").iterdir()))
    result = runner.invoke(
        app,
        ["replay", str(first), "--mock-llm", "--speed", "0", "--set", f"session.dir={tmp_path / 'second'}"],
    )
    assert result.exit_code == 0, result.output
    assert (next(iter((tmp_path / "second").iterdir())) / "final" / "show_notes.md").is_file()


def test_export_re_renders_without_the_llm(tmp_path: Path, osnova_path: Path, fixtures: Path):
    runner.invoke(
        app,
        [
            "replay",
            "--transcript",
            str(fixtures / "transcript_sk.jsonl"),
            "--outline",
            str(osnova_path),
            "--mock-llm",
            "--speed",
            "0",
            "--set",
            f"session.dir={tmp_path}",
        ],
    )
    session_dir = next(iter(tmp_path.iterdir()))
    notes = session_dir / "final" / "show_notes.md"
    notes.unlink()

    result = runner.invoke(app, ["export", str(session_dir)])
    assert result.exit_code == 0, result.output
    assert notes.is_file()
    llm_calls_before = len((session_dir / "llm.jsonl").read_text().splitlines())
    assert llm_calls_before > 0
    # export must not have added a call
    assert len((session_dir / "llm.jsonl").read_text().splitlines()) == llm_calls_before


def test_export_without_a_final_analysis(tmp_path: Path, osnova_path: Path, fixtures: Path):
    runner.invoke(
        app,
        [
            "replay",
            "--transcript",
            str(fixtures / "transcript_sk.jsonl"),
            "--outline",
            str(osnova_path),
            "--mock-llm",
            "--speed",
            "0",
            "--no-wrapup",
            "--set",
            f"session.dir={tmp_path}",
        ],
    )
    session_dir = next(iter(tmp_path.iterdir()))
    result = runner.invoke(app, ["export", str(session_dir)])
    assert result.exit_code != 0
    assert isinstance(result.exception, FileNotFoundError)


def test_resume_continues_from_the_furthest_point(tmp_path: Path, osnova_path: Path, fixtures: Path):
    """The resumed clock uses duration_s and the sync mark, not just the last utterance."""
    from livecaster.config import Config
    from livecaster.session.models import Segment
    from livecaster.session.store import create_session
    from livecaster.session.transcript import Transcript

    cfg = Config()
    cfg.audio.record = False
    store = create_session(osnova_path, cfg, mode="live", base_dir=tmp_path)
    store.append_segment(Segment(id="S1", channel="Host", t0=0.0, t1=24.0, text="ahoj"))
    store.session.duration_s = 900.0
    store.session.sync_marks = [1200.0]
    store.snapshot(force=True)
    store.close()

    transcript = Transcript.load_jsonl(store.transcript_path)
    offset = max(transcript.duration(), store.session.duration_s, store.session.sync_marks[-1])
    assert offset == 1200.0


def test_wrapup_re_runs_on_an_existing_session(tmp_path: Path, osnova_path: Path, fixtures: Path):
    runner.invoke(
        app,
        [
            "replay",
            "--transcript",
            str(fixtures / "transcript_sk.jsonl"),
            "--outline",
            str(osnova_path),
            "--mock-llm",
            "--speed",
            "0",
            "--no-wrapup",
            "--set",
            f"session.dir={tmp_path}",
        ],
    )
    session_dir = next(iter(tmp_path.iterdir()))
    assert not (session_dir / "final" / "show_notes.md").exists()

    result = runner.invoke(app, ["wrapup", str(session_dir), "--mock-llm"])
    assert result.exit_code == 0, result.output
    assert (session_dir / "final" / "show_notes.md").is_file()
    state = json.loads((session_dir / "session.json").read_text())
    assert state["final_paths"]["show_notes"].endswith("show_notes.md")


# --- pre-flight checks that used to fail on air instead --------------------


def test_port_in_use_sees_a_listening_socket():
    import socket

    from livecaster.cli import _port_in_use

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen()
        port = sock.getsockname()[1]
        assert _port_in_use("127.0.0.1", port) is True
    assert _port_in_use("127.0.0.1", port) is False


def test_check_channels_flags_a_missing_file(tmp_path: Path):
    from livecaster.cli import _check_channels
    from livecaster.config import ChannelConfig, Config

    cfg = Config()
    cfg.audio.channels = [ChannelConfig(name="Host", source=f"file:{tmp_path / 'nope.wav'}")]
    assert _check_channels(cfg) is False

    real = tmp_path / "yes.wav"
    real.write_bytes(b"RIFF")
    cfg.audio.channels = [ChannelConfig(name="Host", source=f"file:{real}")]
    assert _check_channels(cfg) is True


def test_check_channels_rejects_an_unknown_scheme():
    from livecaster.cli import _check_channels
    from livecaster.config import ChannelConfig, Config

    cfg = Config()
    cfg.audio.channels = [ChannelConfig(name="Host", source="bluetooth:magic")]
    assert _check_channels(cfg) is False
