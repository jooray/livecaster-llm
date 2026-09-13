"""WebSocket message models (SPEC §7.8)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from livecaster.session.models import Patch, Segment, Session

# --- server -> client -------------------------------------------------------


class Hello(BaseModel):
    type: Literal["hello"] = "hello"
    build_id: str
    session_id: str
    config_summary: dict[str, Any] = Field(default_factory=dict)


class StateMessage(BaseModel):
    type: Literal["state"] = "state"
    session: Session


class PatchMessage(BaseModel):
    type: Literal["patch"] = "patch"
    nodes: dict[str, Any] = Field(default_factory=dict)
    suggestions: Any | None = None
    mentions: Any | None = None
    usage: Any | None = None
    language: str | None = None
    session_status: str | None = None

    @classmethod
    def of(cls, patch: Patch) -> PatchMessage:
        return cls(
            nodes={k: v.model_dump() for k, v in patch.nodes.items()},
            suggestions=patch.suggestions.model_dump() if patch.suggestions else None,
            mentions=[m.model_dump() for m in patch.mentions] if patch.mentions is not None else None,
            usage=patch.usage.model_dump() if patch.usage else None,
            language=patch.language,
            session_status=patch.session_status,
        )


class SegmentMessage(BaseModel):
    type: Literal["segment"] = "segment"
    segment: Segment


class StatusMessage(BaseModel):
    type: Literal["status"] = "status"
    audio: dict[str, float] = Field(default_factory=dict)
    stt: dict[str, Any] = Field(default_factory=dict)
    llm: dict[str, Any] = Field(default_factory=dict)
    clock: float = 0.0
    session_status: str = "idle"
    sync_marks: list[float] = Field(default_factory=list)


class ToastMessage(BaseModel):
    type: Literal["toast"] = "toast"
    level: Literal["info", "warn", "error", "success"] = "info"
    text: str = ""


class DoneMessage(BaseModel):
    """Finish completed: the UI shows a completion card with these numbers."""

    type: Literal["done"] = "done"
    paths: dict[str, str] = Field(default_factory=dict)
    dir: str = ""
    duration_s: float = 0.0
    cost_usd: float = 0.0
    ticks: int = 0
    error: str | None = None


# --- client -> server -------------------------------------------------------


class MarkAction(BaseModel):
    type: Literal["mark"]
    node_id: str
    status: Literal["covered", "untouched", "skipped"]


class PinAction(BaseModel):
    type: Literal["pin"]
    node_id: str
    pinned: bool = True


class SyncMarkAction(BaseModel):
    type: Literal["sync_mark"]


class ControlAction(BaseModel):
    type: Literal["control"]
    action: Literal["start", "pause", "resume", "finish", "tick_now", "reload_outline"]


class SelectAction(BaseModel):
    type: Literal["select"]
    node_id: str | None = None


class SetLanguageAction(BaseModel):
    type: Literal["set_language"]
    language: str = "auto"


class SetTicksAction(BaseModel):
    """Retune the tick loop live. Bounds keep a slip of the keyboard from either
    hammering the API or silencing the co-pilot for the rest of the episode."""

    type: Literal["set_ticks"]
    interval_s: float = Field(ge=5, le=600)
    min_new_words: int = Field(ge=0, le=500)
    burst_words: int = Field(ge=10, le=2000)


class ChannelSpec(BaseModel):
    """One audio channel as the Settings dialog sends it back.

    The bounds are the ones a slip of the mouse could cross; anything the UI
    cannot produce is still rejected here, because `/api/control` is open to
    anything on the machine.
    """

    name: str = Field(min_length=1, max_length=40)
    source: str = Field(min_length=1, max_length=400)
    channel_index: int | None = Field(default=None, ge=0, le=63)
    is_direct: bool = False
    record: bool = True


class SetAudioAction(BaseModel):
    type: Literal["set_audio"]
    channels: list[ChannelSpec] = Field(min_length=1, max_length=8)


class SetTargetAction(BaseModel):
    """Set or clear the episode target. Null clears it; the upper bound is a day,
    which is past any episode and short of a number that breaks the arithmetic."""

    type: Literal["set_target"]
    target_minutes: float | None = Field(default=None, gt=0, le=24 * 60)


class SetModelsAction(BaseModel):
    """Empty strings mean "leave this one alone"; `stt_model` is cleared by null."""

    type: Literal["set_models"]
    tick_model: str = Field(default="", max_length=120)
    final_model: str = Field(default="", max_length=120)
    stt_engine: str = Field(default="", max_length=40)
    stt_model: str | None = Field(default=None, max_length=200)


ClientMessage = (
    MarkAction
    | PinAction
    | SyncMarkAction
    | ControlAction
    | SelectAction
    | SetLanguageAction
    | SetTicksAction
    | SetAudioAction
    | SetModelsAction
    | SetTargetAction
)


def parse_client_message(data: dict[str, Any]) -> ClientMessage:
    kind = data.get("type")
    table: dict[str, type[BaseModel]] = {
        "mark": MarkAction,
        "pin": PinAction,
        "sync_mark": SyncMarkAction,
        "control": ControlAction,
        "select": SelectAction,
        "set_language": SetLanguageAction,
        "set_ticks": SetTicksAction,
        "set_audio": SetAudioAction,
        "set_models": SetModelsAction,
        "set_target": SetTargetAction,
    }
    model = table.get(str(kind))
    if model is None:
        raise ValueError(f"unknown message type {kind!r}")
    return model.model_validate(data)  # type: ignore[return-value]
