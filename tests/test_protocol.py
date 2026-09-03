"""WebSocket message validation and fan-out (M3)."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from livecaster.server.protocol import (
    ControlAction,
    DoneMessage,
    Hello,
    MarkAction,
    PatchMessage,
    SegmentMessage,
    StatusMessage,
    ToastMessage,
    parse_client_message,
)
from livecaster.server.ws import MAX_QUEUE, ConnectionManager
from livecaster.session.models import HotInfo, Mention, NodeState, Patch, Segment, Suggestions, Usage


def test_client_messages_round_trip():
    assert isinstance(
        parse_client_message({"type": "mark", "node_id": "T5", "status": "covered"}), MarkAction
    )
    assert isinstance(parse_client_message({"type": "control", "action": "finish"}), ControlAction)
    assert parse_client_message({"type": "pin", "node_id": "T9"}).pinned is True
    assert parse_client_message({"type": "sync_mark"}).type == "sync_mark"
    assert parse_client_message({"type": "select", "node_id": None}).node_id is None


@pytest.mark.parametrize(
    "data",
    [
        {"type": "nope"},
        {},
        {"type": "mark", "node_id": "T5", "status": "burned"},
        {"type": "control", "action": "explode"},
        {"type": "mark", "status": "covered"},
    ],
)
def test_bad_client_messages_are_rejected(data):
    with pytest.raises((ValueError, ValidationError)):
        parse_client_message(data)


def test_patch_message_serializes_a_patch():
    patch = Patch(
        nodes={"T5": NodeState(id="T5", status="covered", covered_at=12.0, hot=None)},
        suggestions=Suggestions(),
        mentions=[Mention(id="M1", kind="book", text="Breath")],
        usage=Usage(ticks=3, cost_usd=0.01),
        language="sk",
    )
    payload = json.loads(PatchMessage.of(patch).model_dump_json())
    assert payload["type"] == "patch"
    assert payload["nodes"]["T5"]["status"] == "covered"
    assert payload["mentions"][0]["text"] == "Breath"
    assert payload["usage"]["ticks"] == 3
    assert payload["language"] == "sk"


def test_patch_message_of_an_empty_patch():
    payload = json.loads(PatchMessage.of(Patch()).model_dump_json())
    assert payload["nodes"] == {}
    assert payload["mentions"] is None


def test_server_messages_carry_their_type():
    assert json.loads(Hello(build_id="abc", session_id="s").model_dump_json())["type"] == "hello"
    assert json.loads(StatusMessage().model_dump_json())["type"] == "status"
    assert json.loads(ToastMessage(level="warn", text="x").model_dump_json())["level"] == "warn"
    assert json.loads(DoneMessage(paths={"a": "b"}).model_dump_json())["paths"] == {"a": "b"}
    seg = Segment(id="S1", channel="Host", t0=0, t1=1, text="ahoj")
    assert json.loads(SegmentMessage(segment=seg).model_dump_json())["segment"]["text"] == "ahoj"


def test_hot_info_survives_serialization():
    patch = Patch(nodes={"T29": NodeState(id="T29", hot=HotInfo(score=0.9, reason="r", segue="s", rank=1))})
    payload = json.loads(PatchMessage.of(patch).model_dump_json())
    assert payload["nodes"]["T29"]["hot"] == {
        "score": 0.9,
        "reason": "r",
        "segue": "s",
        "since_t": 0.0,
        "rank": 1,
    }


# --- fan-out --------------------------------------------------------------


class FakeWS:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_text(self, payload: str) -> None:
        self.sent.append(payload)


def test_broadcast_serializes_once_for_every_client():
    manager = ConnectionManager()
    a, b = manager.add(FakeWS()), manager.add(FakeWS())
    manager.broadcast(ToastMessage(text="hello"))
    assert a.queue.qsize() == 1 and b.queue.qsize() == 1
    assert json.loads(a.queue.get_nowait())["text"] == "hello"


def test_slow_clients_are_dropped():
    manager = ConnectionManager()
    conn = manager.add(FakeWS())
    for _ in range(MAX_QUEUE + 2):
        manager.broadcast(ToastMessage(text="x"))
    assert manager.count == 0
    assert conn.alive is False


def test_broadcast_without_clients_is_a_no_op():
    ConnectionManager().broadcast(ToastMessage(text="x"))
