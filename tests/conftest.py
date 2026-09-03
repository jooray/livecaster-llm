from __future__ import annotations

from pathlib import Path

import pytest

from livecaster.config import ChannelConfig, Config
from livecaster.outline.parser import parse_outline_file
from livecaster.session.store import SessionStore, create_session

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures() -> Path:
    return FIXTURES


@pytest.fixture
def osnova_path() -> Path:
    return FIXTURES / "osnova.md"


@pytest.fixture
def outline(osnova_path: Path):
    return parse_outline_file(osnova_path)


@pytest.fixture
def config() -> Config:
    cfg = Config()
    cfg.audio.record = False
    cfg.llm.preflight = False
    cfg.ui.open_browser = False
    return cfg


@pytest.fixture
def store(tmp_path: Path, osnova_path: Path, config: Config) -> SessionStore:
    config.audio.channels = [
        ChannelConfig(name="Host", source="file:", record=False),
        ChannelConfig(name="Guest", source="file:", record=False, is_direct=True),
    ]
    return create_session(osnova_path, config, mode="replay", base_dir=tmp_path)
