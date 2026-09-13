"""Config layering and time helpers (M0)."""

from __future__ import annotations

from pathlib import Path

import pytest

from livecaster.config import Config, load_config
from livecaster.timeutil import (
    ManualClock,
    SessionClock,
    atomic_write_json,
    fmt_hms,
    fmt_srt_time,
)


def test_defaults_match_the_spec():
    cfg = Config()
    assert cfg.llm.base_url == "https://api.venice.ai/api/v1"
    assert cfg.llm.tick_model == "deepseek-v4-flash-0731-fast"
    assert cfg.llm.reasoning_effort_tick == "none"
    assert cfg.llm.tick_interval_s == 25
    assert cfg.llm.min_new_words == 25
    assert cfg.llm.burst_words == 120
    assert cfg.llm.transcript_window_words == 1200
    assert cfg.llm.cover_threshold == 0.7
    assert cfg.llm.touch_threshold == 0.4
    assert cfg.llm.manual_lock_minutes == 10
    assert cfg.stt.silence_ms == 600
    assert cfg.stt.max_utterance_s == 20
    assert cfg.ui.host == "127.0.0.1" and cfg.ui.port == 8766
    assert cfg.wrapup.resolve_links is False
    assert [c.name for c in cfg.audio.channels] == ["Host"]


def test_file_then_env_then_set(tmp_path: Path, monkeypatch):
    path = tmp_path / "livecaster.toml"
    path.write_text(
        '[llm]\ntick_interval_s = 10\ntick_model = "from-file"\n'
        '[[audio.channels]]\nname = "Host"\nsource = "device:1"\n'
        '[[audio.channels]]\nname = "Guest"\nsource = "device:2"\nis_direct = true\n',
        encoding="utf-8",
    )
    cfg = load_config(path)
    assert cfg.llm.tick_interval_s == 10
    assert [c.name for c in cfg.audio.channels] == ["Host", "Guest"]
    assert cfg.audio.channels[1].is_direct is True

    monkeypatch.setenv("LIVECASTER_LLM__TICK_MODEL", "from-env")
    cfg = load_config(path)
    assert cfg.llm.tick_model == "from-env"

    cfg = load_config(path, ["llm.tick_model=from-set", "llm.temperature=0.9", "ui.open_browser=false"])
    assert cfg.llm.tick_model == "from-set"
    assert cfg.llm.temperature == 0.9
    assert cfg.ui.open_browser is False


def test_set_keeps_none_as_a_string():
    """`reasoning_effort=none` is a value, not a null."""
    cfg = load_config(None, ["llm.reasoning_effort_final=none", "llm.reasoning_effort_tick=high"])
    assert cfg.llm.reasoning_effort_final == "none"
    assert cfg.llm.reasoning_effort_tick == "high"


@pytest.mark.parametrize(
    "raw,expected",
    [("true", True), ("off", False), ("null", None), ("none", "none"), ("12", 12), ("0.5", 0.5), ("x", "x")],
)
def test_value_coercion(raw: str, expected):
    from livecaster.config import _coerce

    assert _coerce(raw) == expected


def test_set_requires_key_value():
    with pytest.raises(ValueError):
        load_config(None, ["nonsense"])


def test_summary_has_no_secrets():
    summary = Config().summary()
    assert set(summary) == {
        "mode",
        "channels",
        "sources",
        "stt_engine",
        "stt_model",
        "language",
        "tick_model",
        "final_model",
        "tick_interval_s",
    }
    assert "key" not in str(summary).lower()


@pytest.mark.parametrize(
    "seconds,expected",
    [(0, "00:00:00"), (61.4, "00:01:01"), (3671, "01:01:11"), (-5, "00:00:00"), (None, "--:--:--")],
)
def test_fmt_hms(seconds, expected):
    assert fmt_hms(seconds) == expected


def test_fmt_srt_time():
    assert fmt_srt_time(0) == "00:00:00,000"
    assert fmt_srt_time(3661.5) == "01:01:01,500"
    assert fmt_srt_time(-1) == "00:00:00,000"


def test_session_clock_pauses():
    clock = SessionClock()
    assert clock.now() >= 0
    clock.pause()
    paused_at = clock.now()
    assert clock.paused
    assert clock.now() == paused_at
    clock.resume()
    assert not clock.paused
    assert clock.now() >= paused_at


def test_session_clock_offset_for_resume():
    clock = SessionClock(offset=100.0)
    assert clock.now() >= 100.0


def test_manual_clock():
    clock = ManualClock()
    clock.set(42.0)
    assert clock.now() == 42.0
    clock.advance(8.0)
    assert clock.now() == 50.0


def test_atomic_write_json_leaves_no_temp_file(tmp_path: Path):
    target = tmp_path / "nested" / "state.json"
    atomic_write_json(target, {"a": 1})
    assert target.read_text(encoding="utf-8").strip().startswith("{")
    assert list(tmp_path.rglob("*.tmp")) == []
