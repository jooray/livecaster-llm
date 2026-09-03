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


ClientMessage = MarkAction | PinAction | SyncMarkAction | ControlAction | SelectAction


def parse_client_message(data: dict[str, Any]) -> ClientMessage:
    kind = data.get("type")
    table: dict[str, type[BaseModel]] = {
        "mark": MarkAction,
        "pin": PinAction,
        "sync_mark": SyncMarkAction,
        "control": ControlAction,
        "select": SelectAction,
    }
    model = table.get(str(kind))
    if model is None:
        raise ValueError(f"unknown message type {kind!r}")
    return model.model_validate(data)  # type: ignore[return-value]
