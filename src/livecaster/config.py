"""Configuration model and loader (SPEC §10)."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

CONFIG_FILENAME = "livecaster.toml"

#: Venice accepts none/low/high; the Anthropic API adds medium/xhigh/max. Each
#: client narrows an unsupported value to the nearest one it can send.
ReasoningEffort = Literal["none", "low", "medium", "high", "xhigh", "max"]


class ChannelConfig(BaseModel):
    name: str = "Host"
    source: str = "device:default"
    channel_index: int | None = None
    is_direct: bool = False
    record: bool = True


class AudioConfig(BaseModel):
    mode: Literal["live", "remote"] = "live"
    record: bool = True
    record_native: bool = False
    channels: list[ChannelConfig] = Field(default_factory=lambda: [ChannelConfig()])


class STTConfig(BaseModel):
    engine: str = "auto"  # auto | parakeet-mlx | onnx-asr | whisper-mlx | faster-whisper | mock
    model: str = ""
    language: str = "auto"
    vad_threshold: float = 0.5
    silence_ms: int = 600
    min_speech_ms: int = 300
    max_utterance_s: float = 20.0
    preroll_ms: int = 300


class ProviderConfig(BaseModel):
    """One LLM vendor. `kind` decides the wire protocol, not the vendor name."""

    kind: Literal["openai", "anthropic"] = "openai"
    base_url: str = ""
    api_key_env: str = ""
    #: Venice-only request fields (system-prompt suppression, web search). Sending
    #: them to another OpenAI-compatible endpoint is a 400, so it is opt-in.
    venice_extensions: bool = False


def _default_providers() -> dict[str, ProviderConfig]:
    return {
        "venice": ProviderConfig(
            kind="openai",
            base_url="https://api.venice.ai/api/v1",
            api_key_env="VENICE_API_KEY",
            venice_extensions=True,
        ),
        "anthropic": ProviderConfig(kind="anthropic", api_key_env="ANTHROPIC_API_KEY"),
        "openai": ProviderConfig(
            kind="openai", base_url="https://api.openai.com/v1", api_key_env="OPENAI_API_KEY"
        ),
    }


class LLMConfig(BaseModel):
    base_url: str = "https://api.venice.ai/api/v1"
    #: Where a bare model name (one without a `provider:` prefix) is sent.
    default_provider: str = "venice"
    providers: dict[str, ProviderConfig] = Field(default_factory=_default_providers)
    # Measured on 2026-09-03: the plain flash model needs ~38 s per tick, the -fast
    # variant ~9 s. See DECISIONS.md (D8).
    tick_model: str = "deepseek-v4-flash-0731-fast"
    # Sonnet 5 through Venice, so it draws on the same prepaid credits. Prefix a
    # model with a provider to send it elsewhere: `anthropic:claude-sonnet-5`.
    final_model: str = "claude-sonnet-5"
    tick_interval_s: float = 25.0
    min_new_words: int = 25
    burst_words: int = 120
    transcript_window_words: int = 1200
    reasoning_effort_tick: ReasoningEffort = "none"
    reasoning_effort_final: ReasoningEffort = "high"
    temperature: float = 0.2
    cover_threshold: float = 0.7
    touch_threshold: float = 0.4
    heading_cover_threshold: float = 0.85
    manual_lock_minutes: float = 10.0
    preflight: bool = True
    tick_timeout_s: float = 60.0
    final_timeout_s: float = 600.0
    max_tick_tokens: int = 2500
    max_final_tokens: int = 32000


class WrapupConfig(BaseModel):
    resolve_links: bool = False


class UIConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8765
    open_browser: bool = True
    theme: Literal["dark", "light"] = "dark"


class SessionConfig(BaseModel):
    dir: str = "sessions"


class Config(BaseModel):
    audio: AudioConfig = Field(default_factory=AudioConfig)
    stt: STTConfig = Field(default_factory=STTConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    wrapup: WrapupConfig = Field(default_factory=WrapupConfig)
    ui: UIConfig = Field(default_factory=UIConfig)
    session: SessionConfig = Field(default_factory=SessionConfig)

    def summary(self) -> dict[str, Any]:
        """Small, non-secret dict for the UI `hello` message."""
        return {
            "mode": self.audio.mode,
            "channels": [c.name for c in self.audio.channels],
            "stt_engine": self.stt.engine,
            "language": self.stt.language,
            "tick_model": self.llm.tick_model,
            "final_model": self.llm.final_model,
            "tick_interval_s": self.llm.tick_interval_s,
        }


def find_config_file(explicit: str | Path | None = None) -> Path | None:
    if explicit:
        p = Path(explicit).expanduser()
        return p if p.is_file() else None
    candidates = [
        Path.cwd() / CONFIG_FILENAME,
        Path.home() / ".config" / "livecaster" / CONFIG_FILENAME,
    ]
    for c in candidates:
        if c.is_file():
            return c
    return None


def _coerce(value: str) -> Any:
    low = value.strip().lower()
    if low in {"true", "yes", "on"}:
        return True
    if low in {"false", "no", "off"}:
        return False
    # "none" is a real value here (reasoning_effort), so only "null" means null.
    if low == "null":
        return None
    try:
        if "." in value or "e" in low:
            return float(value)
        return int(value)
    except ValueError:
        return value


def _deep_merge(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _apply_override(data: dict[str, Any], dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    cur = data
    for p in parts[:-1]:
        nxt = cur.get(p)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[p] = nxt
        cur = nxt
    cur[parts[-1]] = value


def env_overrides() -> dict[str, Any]:
    """`LIVECASTER_LLM__TICK_MODEL=x` -> {"llm": {"tick_model": "x"}}."""
    data: dict[str, Any] = {}
    for key, raw in os.environ.items():
        if not key.startswith("LIVECASTER_"):
            continue
        path = key[len("LIVECASTER_") :].lower().replace("__", ".")
        if not path:
            continue
        _apply_override(data, path, _coerce(raw))
    return data


def load_config(
    path: str | Path | None = None,
    overrides: list[str] | None = None,
) -> Config:
    """Merge defaults <- config file <- environment <- ``--set`` overrides."""
    data: dict[str, Any] = {}
    cfg_file = find_config_file(path)
    if cfg_file:
        with cfg_file.open("rb") as fh:
            data = tomllib.load(fh)
    data = _deep_merge(data, env_overrides())
    for item in overrides or []:
        if "=" not in item:
            raise ValueError(f"--set expects key=value, got {item!r}")
        key, _, value = item.partition("=")
        _apply_override(data, key.strip(), _coerce(value))
    return Config.model_validate(data)
