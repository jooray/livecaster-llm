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

from livecaster.config import ChannelConfig
from livecaster.engine import Engine
from livecaster.log import get_logger
from livecaster.server.auth import (
    COOKIE_NAME,
    HEADER_NAME,
    OPEN_PATHS,
    QUERY_NAME,
    is_loopback,
    token_from,
    token_ok,
)
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
#: An outline is prose. Anything larger than this is not one, and the browser
#: should not be able to fill the session directory by accident either.
MAX_OUTLINE_BYTES = 2_000_000


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


def settings_payload(engine: Engine) -> dict[str, Any]:
    """Everything the Settings dialog needs, in one round-trip (FR-36, FR-37).

    Enumerating devices talks to PortAudio and can block for a moment, so the
    route calls this in a thread.
    """
    from livecaster.audio.devices import audiotee_binary, audiotee_candidates, list_devices
    from livecaster.llm.pricing import PRICING
    from livecaster.stt.registry import engine_catalogue

    cfg = engine.config
    devices: list[dict[str, Any]] = []
    device_error: str | None = None
    try:
        for d in list_devices():
            devices.append(
                {
                    "index": d.index,
                    "name": d.name,
                    "channels": d.max_input_channels,
                    "samplerate": round(d.default_samplerate),
                    "hostapi": d.hostapi,
                    "is_default": d.is_default,
                    "headset_mode": d.is_headset_mode,
                }
            )
    except Exception as exc:
        device_error = f"{type(exc).__name__}: {exc}"
        log.warning("device enumeration failed: %s", device_error)

    session = engine.store.session
    return {
        "audio": {
            "mode": cfg.audio.mode,
            "record": cfg.audio.record,
            "channels": [c.model_dump() for c in cfg.audio.channels],
            "devices": devices,
            "device_error": device_error,
            "audiotee": audiotee_candidates(),
            "audiotee_available": bool(audiotee_binary()),
        },
        "stt": {
            "engine": cfg.stt.engine,
            "model": cfg.stt.model,
            "language": cfg.stt.language,
            "engines": engine_catalogue(),
        },
        "llm": {
            "tick_model": cfg.llm.tick_model,
            "final_model": cfg.llm.final_model,
            "default_provider": cfg.llm.default_provider,
            "providers": sorted(cfg.llm.providers),
            "known_models": sorted(PRICING),
        },
        "outline": {
            "path": session.outline_path,
            "items": len(engine.store.outline.leaves()),
            "nodes": len(session.outline),
        },
        "session_status": session.status,
    }


def create_app(engine: Engine, ui_dir: Path | None = None, token: str | None = None) -> FastAPI:
    """Build the app. ``token`` is set only when the server is bound past loopback
    (FR-42); with it None nothing about the default local server changes."""
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
    app.state.token = token

    if token is not None:

        @app.middleware("http")
        async def require_token(request: Request, call_next: Any) -> Any:
            """Gate every off-machine request. Loopback is the host's own browser."""
            if is_loopback(request.client.host if request.client else None):
                return await call_next(request)
            if request.url.path in OPEN_PATHS:
                return await call_next(request)
            from_query = request.query_params.get(QUERY_NAME)
            presented = token_from(
                query=from_query,
                header=request.headers.get(HEADER_NAME),
                cookie=request.cookies.get(COOKIE_NAME),
            )
            if not token_ok(token, presented):
                return PlainTextResponse(
                    "This Livecaster needs the key printed in its terminal. "
                    f"Open the address it shows, including the ?{QUERY_NAME}=... part.",
                    status_code=401,
                )
            response = await call_next(request)
            if from_query:
                # Remember it, so /static, /api and /ws work without the query string.
                response.set_cookie(
                    COOKIE_NAME, token, httponly=True, samesite="strict", max_age=60 * 60 * 24
                )
            return response

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

    @app.get("/api/settings")
    async def api_settings() -> JSONResponse:
        return JSONResponse(await asyncio.to_thread(settings_payload, engine))

    @app.get("/api/models")
    async def api_models(provider: str | None = None) -> JSONResponse:
        """The provider's live model list, for the Settings dialog's suggestions.

        Never an error status: the dialog works from the built-in list when the
        network or the key is not there, and must not look broken because of it.
        """
        lister = getattr(engine.client, "list_models", None)
        name = provider or engine.config.llm.default_provider
        if lister is None:
            return JSONResponse({"provider": name, "models": [], "error": "this client cannot list models"})
        try:
            payload = await lister(provider)
        except Exception as exc:
            log.info("model list from %s failed: %s", name, exc)
            return JSONResponse({"provider": name, "models": [], "error": f"{type(exc).__name__}: {exc}"})
        ids = sorted({str(m["id"]) for m in payload.get("data") or [] if m.get("id")})
        return JSONResponse({"provider": name, "models": ids})

    @app.post("/api/outline")
    async def api_outline(request: Request) -> JSONResponse:
        """Adopt an outline uploaded from the browser (FR-38).

        The body is JSON rather than a multipart form so that reading a `.md` in
        the browser and posting its text needs no extra server dependency.
        """
        data = await request.json()
        text = data.get("text")
        if not isinstance(text, str) or not text.strip():
            return JSONResponse({"error": "no outline text"}, status_code=400)
        if len(text.encode("utf-8")) > MAX_OUTLINE_BYTES:
            return JSONResponse({"error": "that file is too large to be an outline"}, status_code=413)
        filename = str(data.get("filename") or "")
        if not engine.load_outline_text(text, filename):
            return JSONResponse({"error": "the outline could not be loaded"}, status_code=400)
        return JSONResponse(
            {
                "ok": True,
                "path": engine.store.session.outline_path,
                "items": len(engine.store.outline.leaves()),
            }
        )

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
        if token is not None and not is_loopback(websocket.client.host if websocket.client else None):
            presented = token_from(
                query=websocket.query_params.get(QUERY_NAME),
                header=websocket.headers.get(HEADER_NAME),
                cookie=websocket.cookies.get(COOKIE_NAME),
            )
            if not token_ok(token, presented):
                log.warning("refused a websocket without a key from %s", websocket.client)
                await websocket.close(code=1008)
                return
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
    elif kind == "set_audio":
        await engine.set_channels(
            [ChannelConfig(**c.model_dump()) for c in message.channels]  # type: ignore[union-attr]
        )
    elif kind == "set_models":
        engine.set_models(
            tick_model=message.tick_model,  # type: ignore[union-attr]
            final_model=message.final_model,  # type: ignore[union-attr]
            stt_engine=message.stt_engine,  # type: ignore[union-attr]
            stt_model=message.stt_model,  # type: ignore[union-attr]
        )
    elif kind == "set_target":
        engine.set_target_minutes(message.target_minutes)  # type: ignore[union-attr]
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
