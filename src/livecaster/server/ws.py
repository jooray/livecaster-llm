"""WebSocket fan-out: one queue per client, slow clients get dropped (PLAN §5.4)."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from pydantic import BaseModel

from livecaster.log import get_logger

log = get_logger(__name__)

MAX_QUEUE = 200


class Connection:
    def __init__(self, websocket: Any) -> None:
        self.ws = websocket
        self.queue: asyncio.Queue[str] = asyncio.Queue(maxsize=MAX_QUEUE + 50)
        self.alive = True

    def offer(self, payload: str) -> bool:
        if self.queue.qsize() > MAX_QUEUE:
            self.alive = False
            return False
        self.queue.put_nowait(payload)
        return True

    async def pump(self) -> None:
        """The only place that writes to the socket, so sends never interleave."""
        while self.alive:
            payload = await self.queue.get()
            try:
                await self.ws.send_text(payload)
            except Exception:
                self.alive = False
                log.debug("websocket send failed, stopping the pump", exc_info=True)
                raise


class ConnectionManager:
    def __init__(self) -> None:
        self.connections: list[Connection] = []

    def add(self, websocket: Any) -> Connection:
        conn = Connection(websocket)
        self.connections.append(conn)
        return conn

    def remove(self, conn: Connection) -> None:
        conn.alive = False
        if conn in self.connections:
            self.connections.remove(conn)

    @property
    def count(self) -> int:
        return len(self.connections)

    def broadcast(self, message: BaseModel | dict[str, Any]) -> None:
        """Serialize once, push to every client queue. Never blocks."""
        if not self.connections:
            return
        if isinstance(message, BaseModel):
            payload = message.model_dump_json()
        else:
            payload = json.dumps(message, ensure_ascii=False, default=str)
        for conn in list(self.connections):
            if not conn.offer(payload):
                log.warning("dropping a slow websocket client")
                self.remove(conn)

    def send(self, conn: Connection, message: BaseModel | dict[str, Any]) -> None:
        """Queue a message for one client; the pump does the actual write."""
        payload = (
            message.model_dump_json()
            if isinstance(message, BaseModel)
            else json.dumps(message, ensure_ascii=False, default=str)
        )
        conn.offer(payload)
