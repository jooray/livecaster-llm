"""FastAPI app: static UI, REST snapshots and the WebSocket (SPEC §7.8)."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from starlette.staticfiles import StaticFiles

from livecaster.engine import Engine
from livecaster.log import get_logger
from livecaster.server.protocol import (
    DoneMessage,
    Hello,
    PatchMessage,
    SegmentMessage,
    StateMessage,
    StatusMessage,
    ToastMessage,
    parse_client_message,
)
from livecaster.server.static import UI_DIR, compute_build_id, index_html
from livecaster.server.ws import ConnectionManager
from livecaster.session.models import Patch, Segment, Session
from livecaster.session.reducer import ManualAction

log = get_logger(__name__)

STATUS_HZ = 5.0


def same_origin(websocket: WebSocket) -> bool:
    """Reject a WebSocket opened by some other page in the host's browser.

    The same-origin policy does not apply to WebSockets, so any site open in a tab
    during a recording could otherwise connect to 127.0.0.1 and send `finish`. A
    non-browser client (curl, the tests) sends no Origin and is left alone.
    """
    origin = websocket.headers.get("origin")
    if origin is None:
        return True
    try:
        netloc = urlsplit(origin).netloc
    except ValueError:
        return False
    return bool(netloc) and netloc == websocket.headers.get("host", "")


class NoStoreStatic(StaticFiles):
    """Static files must never be cached, or a reload would serve the old UI."""

    def file_response(self, *args: Any, **kwargs: Any) -> Any:  # noqa: D102
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-store"
        return response


def create_app(engine: Engine, ui_dir: Path | None = None) -> FastAPI:
    directory = ui_dir or UI_DIR
    build_id = compute_build_id(directory)
    manager = ConnectionManager()

    async def status_loop() -> None:
        try:
            while True:
                await asyncio.sleep(1.0 / STATUS_HZ)
                if manager.count:
                    manager.broadcast(StatusMessage(**engine.status_payload()))
        except asyncio.CancelledError:  # pragma: no cover
            raise

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        await engine.startup()
        task = asyncio.create_task(status_loop(), name="status")
        try:
            yield
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
            await engine.shutdown()

    app = FastAPI(title="Livecaster", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.state.engine = engine
    app.state.manager = manager
    app.state.build_id = build_id

    # --- store events -> websocket ------------------------------------------

    def on_event(kind: str, payload: Any) -> None:
        if kind == "patch" and isinstance(payload, Patch):
            manager.broadcast(PatchMessage.of(payload))
        elif kind == "segment" and isinstance(payload, Segment):
            manager.broadcast(SegmentMessage(segment=payload))
        elif kind == "state" and isinstance(payload, Session):
            manager.broadcast(StateMessage(session=payload))
        elif kind == "toast":
            manager.broadcast(ToastMessage(**payload))
        elif kind == "done":
            manager.broadcast(DoneMessage(**payload))
        elif kind == "llm_error":
            manager.broadcast(ToastMessage(level="warn", text=f"LLM: {payload.get('error', 'error')}"))
        elif kind == "status_changed":
            manager.broadcast(PatchMessage(session_status=str(payload)))

    engine.store.subscribe(on_event)

    # --- routes -------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        return HTMLResponse(
            index_html(build_id, engine.config.ui.theme, directory),
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/api/state")
    async def api_state() -> JSONResponse:
        return JSONResponse(
            {
                "build_id": build_id,
                "session": engine.store.session.model_dump(mode="json"),
                "config": engine.config.summary(),
                "status": engine.status_payload(),
            }
        )

    @app.get("/api/transcript")
    async def api_transcript() -> JSONResponse:
        return JSONResponse([s.model_dump(mode="json") for s in engine.transcript.segments])

    @app.get("/api/health", response_class=PlainTextResponse)
    async def api_health() -> str:
        return "ok"

    @app.get("/api/final")
    async def api_final_index() -> JSONResponse:
        """What the wrap-up wrote, so the UI can show it without touching the disk."""
        return JSONResponse(
            {
                "dir": str(engine.store.dir.resolve()),
                "paths": engine.store.session.final_paths,
                "error": engine.finish_error,
            }
        )

    @app.get("/api/final/{name}", response_class=PlainTextResponse)
    async def api_final_file(name: str) -> PlainTextResponse:
        target = engine.store.session.final_paths.get(name)
        if target is None:
            raise HTTPException(status_code=404, detail=f"no artifact named {name!r}")
        path = Path(target)
        # Only ever serve what the wrap-up itself recorded, and only from this session.
        if not path.is_absolute():
            path = (engine.store.dir.parent.parent / path).resolve()
        if not path.is_file() or engine.store.dir.resolve() not in path.resolve().parents:
            raise HTTPException(status_code=404, detail=f"{name} is not in this session")
        return PlainTextResponse(
            path.read_text(encoding="utf-8"),
            headers={"Cache-Control": "no-store"},
            media_type="text/plain; charset=utf-8",
        )

    @app.post("/api/control")
    async def api_control(request: Request) -> JSONResponse:
        data = await request.json()
        try:
            await handle_client_message(engine, data, manager)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except Exception as exc:
            log.exception("control failed: %s", data)
            manager.broadcast(ToastMessage(level="error", text=f"{type(exc).__name__}: {exc}"))
            return JSONResponse({"error": f"{type(exc).__name__}: {exc}"}, status_code=500)
        return JSONResponse({"ok": True})

    @app.websocket("/ws")
    async def ws_endpoint(websocket: WebSocket) -> None:
        if not same_origin(websocket):
            log.warning("refused a websocket from %s", websocket.headers.get("origin"))
            await websocket.close(code=1008)
            return
        await websocket.accept()
        conn = manager.add(websocket)
        pump = asyncio.create_task(conn.pump())
        try:
            manager.send(
                conn,
                Hello(
                    build_id=build_id,
                    session_id=engine.store.session.id,
                    config_summary=engine.config.summary(),
                ),
            )
            manager.send(conn, StateMessage(session=engine.store.session))
            for segment in engine.transcript.segments[-200:]:
                manager.send(conn, SegmentMessage(segment=segment))
            manager.send(conn, StatusMessage(**engine.status_payload()))
            while True:
                data = await websocket.receive_json()
                try:
                    await handle_client_message(engine, data, manager)
                except ValueError as exc:
                    manager.send(conn, ToastMessage(level="error", text=str(exc)))
                except Exception as exc:
                    log.exception("client message failed: %s", data)
                    manager.send(conn, ToastMessage(level="error", text=f"{type(exc).__name__}: {exc}"))
        except WebSocketDisconnect:
            pass
        except Exception as exc:  # pragma: no cover - client-side failures
            log.debug("websocket closed: %s", exc)
        finally:
            pump.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await pump
            manager.remove(conn)

    app.mount("/static", NoStoreStatic(directory=str(directory)), name="static")
    return app


async def handle_client_message(engine: Engine, data: dict[str, Any], manager: ConnectionManager) -> None:
    message = parse_client_message(data)
    kind = message.type
    if kind == "mark":
        engine.manual(ManualAction(kind="mark", node_id=message.node_id, status=message.status))  # type: ignore[union-attr]
    elif kind == "pin":
        engine.manual(
            ManualAction(kind="pin" if message.pinned else "unpin", node_id=message.node_id)  # type: ignore[union-attr]
        )
    elif kind == "sync_mark":
        engine.sync_mark()
    elif kind == "select":
        pass  # client-side only; accepted so the UI can keep one message shape
    elif kind == "set_language":
        engine.set_language(message.language)  # type: ignore[union-attr]
    elif kind == "set_ticks":
        engine.set_ticks(
            interval_s=message.interval_s,  # type: ignore[union-attr]
            min_new_words=message.min_new_words,  # type: ignore[union-attr]
            burst_words=message.burst_words,  # type: ignore[union-attr]
        )
    elif kind == "control":
        action = message.action  # type: ignore[union-attr]
        if action == "start":
            await engine.start_capture()
        elif action == "pause":
            await engine.pause()
        elif action == "resume":
            await engine.resume()
        elif action == "finish":
            engine.finish_in_background()
        elif action == "tick_now":
            engine.request_tick()
            manager.broadcast(ToastMessage(level="info", text="Tick requested"))
        elif action == "reload_outline":
            engine.reload_outline()
