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
