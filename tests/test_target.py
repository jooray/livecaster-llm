"""The episode target length (FR-41). Unset by default, and unset must stay unset."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from livecaster.config import Config, load_config
from livecaster.server.protocol import parse_client_message


def test_no_target_by_default():
    assert Config().session.target_minutes is None
    assert Config().summary()["target_minutes"] is None


def test_set_override_reaches_the_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = load_config(None, ["session.target_minutes=45"])
    assert cfg.session.target_minutes == 45.0


def test_a_fractional_target_survives_the_override(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert load_config(None, ["session.target_minutes=7.5"]).session.target_minutes == 7.5


@pytest.mark.parametrize("bad", [0, -10, 60 * 24 + 1])
def test_a_nonsense_target_is_refused(bad):
    with pytest.raises(ValidationError):
        Config.model_validate({"session": {"target_minutes": bad}})


def test_the_session_carries_the_target_to_the_ui(store, config):
    """The client does the arithmetic, so the number only has to arrive."""
    assert "target_minutes" in store.session.model_dump()


def test_set_target_message_parses():
    msg = parse_client_message({"type": "set_target", "target_minutes": 60})
    assert msg.target_minutes == 60


def test_set_target_clears_with_null():
    assert parse_client_message({"type": "set_target", "target_minutes": None}).target_minutes is None


@pytest.mark.parametrize("bad", [0, -1, 60 * 24 + 1])
def test_set_target_rejects_nonsense(bad):
    with pytest.raises(ValidationError):
        parse_client_message({"type": "set_target", "target_minutes": bad})


def test_engine_sets_and_clears_the_target(store, config, fixtures):
    from livecaster.engine import Engine
    from livecaster.llm.mock import MockLLM
    from livecaster.timeutil import ManualClock

    engine = Engine(store, config, MockLLM(fixtures / "tick_responses"), clock=ManualClock())
    engine.set_target_minutes(50)
    assert store.session.target_minutes == 50.0
    assert config.session.target_minutes == 50.0
    engine.set_target_minutes(None)
    assert store.session.target_minutes is None
