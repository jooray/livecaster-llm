"""HTTP and WebSocket surface of the server (M3)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from livecaster.engine import Engine
from livecaster.llm.mock import MockLLM
from livecaster.server.static import compute_build_id, index_html
from livecaster.session.models import Segment

UI_DIR = Path(__file__).resolve().parents[1] / "src" / "livecaster" / "ui"


@pytest.fixture
def client(store, config, fixtures):
    from livecaster.server.app import create_app
    from livecaster.timeutil import ManualClock

    engine = Engine(store, config, MockLLM(fixtures / "tick_responses"), clock=ManualClock())
    app = create_app(engine)
    app.state.test_engine = engine
    with TestClient(app) as c:
        yield c


def test_index_carries_the_build_id_and_no_service_worker(client):
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.text
    assert "{{BUILD_ID}}" not in body
    assert 'window.BUILD_ID = "' in body
    assert "serviceWorker" not in body
    assert "manifest" not in body


def test_static_files_are_not_cached(client):
    response = client.get("/static/app.js")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert client.get("/static/styles.css").status_code == 200
    assert client.get("/static/vendor/marked.min.js").status_code == 200


def test_api_state(client):
    data = client.get("/api/state").json()
    assert data["session"]["id"]
    assert data["config"]["tick_model"]
    assert len(data["session"]["outline"]) > 40
    assert data["status"]["session_status"] == "idle"


def test_api_transcript_and_health(client):
    assert client.get("/api/health").text == "ok"
    assert client.get("/api/transcript").json() == []


def test_api_control_marks_a_node(client):
    engine = client.app.state.test_engine
    response = client.post("/api/control", json={"type": "mark", "node_id": "T5", "status": "covered"})
    assert response.status_code == 200
    assert engine.store.session.nodes["T5"].status == "covered"


def test_api_control_rejects_junk(client):
    assert client.post("/api/control", json={"type": "explode"}).status_code == 400


def test_websocket_hello_state_and_actions(client):
    engine = client.app.state.test_engine
    engine.add_segment(Segment(id="S1", channel="Host", t0=0, t1=2, text="ahoj"))
    with client.websocket_connect("/ws") as ws:
        hello = ws.receive_json()
        assert hello["type"] == "hello"
        assert hello["build_id"] == compute_build_id(UI_DIR)
        state = ws.receive_json()
        assert state["type"] == "state"
        assert state["session"]["id"] == engine.store.session.id
        segment = ws.receive_json()
        assert segment["type"] == "segment" and segment["segment"]["text"] == "ahoj"
        status = ws.receive_json()
        assert status["type"] == "status"

        ws.send_json({"type": "mark", "node_id": "T6", "status": "skipped"})
        patch = ws.receive_json()
        assert patch["type"] == "patch"
        assert patch["nodes"]["T6"]["status"] == "skipped"


def test_websocket_sync_mark(client):
    engine = client.app.state.test_engine
    with client.websocket_connect("/ws") as ws:
        for _ in range(3):
            ws.receive_json()
        ws.send_json({"type": "sync_mark"})
        message = ws.receive_json()
        while message["type"] == "status":
            message = ws.receive_json()
        assert message["type"] == "toast"
        assert engine.store.session.sync_marks


def test_websocket_reports_bad_messages_without_closing(client):
    with client.websocket_connect("/ws") as ws:
        for _ in range(3):
            ws.receive_json()
        ws.send_json({"type": "nonsense"})
        message = ws.receive_json()
        while message["type"] == "status":
            message = ws.receive_json()
        assert message["type"] == "toast" and message["level"] == "error"


def test_build_id_changes_with_the_ui(tmp_path: Path):
    (tmp_path / "index.html").write_text("<html>{{BUILD_ID}}</html>")
    first = compute_build_id(tmp_path)
    (tmp_path / "app.js").write_text("// new")
    assert compute_build_id(tmp_path) != first
    assert index_html("abc", "dark", tmp_path) == "<html>abc</html>"


def test_index_html_substitutes_the_theme():
    html = index_html("bid", "light", UI_DIR)
    assert 'data-theme="light"' in html
    assert "{{THEME}}" not in html


def test_ui_files_parse_as_expected():
    app_js = (UI_DIR / "app.js").read_text(encoding="utf-8")
    # The live keys, plus the overlays that replaced the tab strip.
    for key in ["j", "k", "c", "x", "p", "m", "t", "d", "q", "l", "n", "w"]:
        assert f'key === "{key}"' in app_js
    assert "location.reload()" in app_js          # FR-27
    assert "localStorage" in app_js
    assert 'action = status === "running" ? "pause"' in app_js
    assert "showUsage" in app_js
    assert "renderPrep" in app_js                 # FR-05: pre-flight questions on demand
    assert "renderResult" in app_js and "/api/final/" in app_js
    assert "splitSections" in app_js              # a show-notes section copies on click
    assert "setDense" in app_js
    assert "set_language" in app_js and "set_ticks" in app_js and "set_target" in app_js
    # The score: lettered rehearsal marks are the jump keys, and a tick that only
    # recolours a line must not rebuild the DOM under someone mid-glance.
    assert "letterFor" in app_js and "jumpTo" in app_js and "state.letters" in app_js
    assert "shapeKey" in app_js and "livePassage" in app_js

    css = (UI_DIR / "styles.css").read_text(encoding="utf-8")
    assert "--body: 18px" in css                  # FR-26 floor
    for state in ["cut", "warm", "skipped", "accent", "hot", "pinned", "current", "selected"]:
        assert f".stave.{state}" in css
    assert ".system.is-rest" in css and ".system.is-live" in css
    assert 'html[data-theme="light"]' in css      # FR-26 light theme
    # Self-hosted faces: the network failing mid-episode must not restyle the page.
    assert "/static/fonts/" in css
    assert "fonts.googleapis.com" not in css and "fonts.gstatic.com" not in css

    html = (UI_DIR / "index.html").read_text(encoding="utf-8")
    for ident in ["edge-up", "usage-body", "lang-status", "result-body", "tick-form",
                  "systems", "overlay-body", "wrap", "budget", "settings-target"]:
        assert json.dumps(ident)[1:-1] in html
    assert "fonts.googleapis.com" not in html
    # The tab strip and the drag-to-resize split are gone with the redesign.
    assert "splitter" not in html and 'class="tab"' not in html


def test_ui_ships_the_fonts_it_asks_for():
    referenced = {
        line.split("/static/fonts/")[1].split(")")[0].strip('"\'')
        for line in (UI_DIR / "styles.css").read_text(encoding="utf-8").splitlines()
        if "/static/fonts/" in line
    }
    assert referenced, "the stylesheet should name its own woff2 files"
    for name in referenced:
        path = UI_DIR / "fonts" / name
        assert path.exists(), f"{name} is referenced but not shipped"
        assert path.stat().st_size > 1000


# --- the wrap-up's files, served to the UI ---------------------------------


def test_api_final_lists_nothing_before_the_wrap_up(client):
    payload = client.get("/api/final").json()
    assert payload["paths"] == {}
    assert payload["dir"].endswith(client.app.state.test_engine.store.dir.name)


def test_api_final_serves_an_artifact(client):
    engine = client.app.state.test_engine
    engine.store.final_dir.mkdir(parents=True, exist_ok=True)
    notes = engine.store.final_dir / "show_notes.md"
    notes.write_text("# Dych\n\nZhrnutie.\n", encoding="utf-8")
    engine.store.session.final_paths = {"show_notes": str(notes)}

    listing = client.get("/api/final").json()
    assert listing["paths"] == {"show_notes": str(notes)}
    body = client.get("/api/final/show_notes")
    assert body.status_code == 200
    assert "Zhrnutie" in body.text
    assert body.headers["cache-control"] == "no-store"


def test_api_final_rejects_an_unknown_name(client):
    assert client.get("/api/final/passwd").status_code == 404


def test_api_final_refuses_to_leave_the_session_directory(client, tmp_path: Path):
    engine = client.app.state.test_engine
    outside = tmp_path / "secret.md"
    outside.write_text("nope", encoding="utf-8")
    engine.store.session.final_paths = {"show_notes": str(outside)}
    assert client.get("/api/final/show_notes").status_code == 404


def test_websocket_sets_the_language(client):
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()  # hello
        ws.receive_json()  # state
        ws.send_json({"type": "set_language", "language": "sk"})
        seen = [ws.receive_json() for _ in range(3)]
    assert any(m.get("type") == "patch" and m.get("language") == "sk" for m in seen)
    assert client.app.state.test_engine.config.stt.language == "sk"


def test_websocket_retunes_the_tick_loop(client):
    engine = client.app.state.test_engine
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()  # hello
        ws.receive_json()  # state
        ws.send_json({"type": "set_ticks", "interval_s": 15, "min_new_words": 40, "burst_words": 200})
        ws.receive_json()
    assert engine.config.llm.tick_interval_s == 15
    assert engine.config.llm.min_new_words == 40
    assert engine.config.llm.burst_words == 200


def test_api_control_rejects_an_absurd_tick_interval(client):
    engine = client.app.state.test_engine
    before = engine.config.llm.tick_interval_s
    response = client.post(
        "/api/control",
        json={"type": "set_ticks", "interval_s": 0.2, "min_new_words": 25, "burst_words": 120},
    )
    assert response.status_code >= 400
    assert engine.config.llm.tick_interval_s == before


# --- the WebSocket is a control channel, so it checks who opened it ---------


def test_websocket_accepts_its_own_origin(client):
    with client.websocket_connect("/ws", headers={"origin": "http://testserver"}) as ws:
        assert ws.receive_json()["type"] == "hello"


def test_websocket_refuses_another_origin(client):
    """Any page open in the host's browser could otherwise press Finish mid-episode."""
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws", headers={"origin": "https://evil.example"}) as ws:
            ws.receive_json()


def test_same_origin_allows_a_client_without_an_origin_header():
    from livecaster.server.app import same_origin

    class FakeWS:
        headers: dict[str, str] = {}

    assert same_origin(FakeWS()) is True


# --- settings: audio, models and the outline, all from the browser ----------


def test_api_settings_describes_the_machine(client):
    data = client.get("/api/settings").json()
    assert [c["name"] for c in data["audio"]["channels"]] == ["Host", "Guest"]
    assert isinstance(data["audio"]["devices"], list)
    assert data["llm"]["tick_model"]
    assert "deepseek-v4-flash-0731-fast" in data["llm"]["known_models"]
    keys = {e["key"] for e in data["stt"]["engines"]}
    assert {"auto", "whisper-mlx", "faster-whisper"} <= keys
    assert data["outline"]["items"] > 40


def test_api_models_never_fails_the_dialog(client):
    """The mock client cannot list models; the dialog still has to open."""
    data = client.get("/api/models").json()
    assert data["models"] == []
    assert data["error"]


def test_set_audio_repoints_the_channels(client):
    engine = client.app.state.test_engine
    response = client.post(
        "/api/control",
        json={
            "type": "set_audio",
            "channels": [{"name": "Room", "source": "device:auto", "is_direct": False}],
        },
    )
    assert response.status_code == 200
    assert [c.source for c in engine.config.audio.channels] == ["device:auto"]
    assert [c.name for c in engine.store.session.channels] == ["Room"]
    assert engine.transcript.direct_channels == set()


def test_set_audio_rejects_a_nameless_or_duplicate_channel(client):
    engine = client.app.state.test_engine
    before = [c.name for c in engine.config.audio.channels]
    assert client.post("/api/control", json={"type": "set_audio", "channels": []}).status_code >= 400
    duplicate = {
        "type": "set_audio",
        "channels": [{"name": "Host", "source": "device:auto"}, {"name": "Host", "source": "device:1"}],
    }
    assert client.post("/api/control", json=duplicate).status_code >= 400
    assert [c.name for c in engine.config.audio.channels] == before


def test_set_models_swaps_the_llm_and_defers_the_stt(client):
    engine = client.app.state.test_engine
    response = client.post(
        "/api/control",
        json={
            "type": "set_models",
            "tick_model": "deepseek-v4-pro-0813",
            "final_model": "anthropic:claude-opus-5",
            "stt_engine": "mock",
            "stt_model": "",
        },
    )
    assert response.status_code == 200
    assert engine.config.llm.tick_model == "deepseek-v4-pro-0813"
    assert engine.config.llm.final_model == "anthropic:claude-opus-5"
    assert engine.config.stt.engine == "mock"


def test_set_models_refuses_an_engine_that_does_not_exist(client):
    engine = client.app.state.test_engine
    before = engine.config.stt.engine
    response = client.post(
        "/api/control", json={"type": "set_models", "stt_engine": "telepathy"}
    )
    assert response.status_code >= 400
    assert engine.config.stt.engine == before


def test_upload_outline_replaces_the_map(client):
    engine = client.app.state.test_engine
    text = "# New show\n\n- First thing\n- Second thing\n"
    response = client.post("/api/outline", json={"filename": "new.md", "text": text})
    assert response.status_code == 200
    assert response.json()["items"] == 2
    assert [n.text for n in engine.store.outline.leaves()] == ["First thing", "Second thing"]
    # It lands in the session directory, so the recording travels with its outline.
    saved = engine.store.dir / "outline.uploaded.md"
    assert saved.read_text(encoding="utf-8") == text
    assert engine.store.session.outline_path == str(saved.resolve())


def test_upload_outline_rejects_nothing_and_too_much(client):
    assert client.post("/api/outline", json={"text": "   "}).status_code == 400
    assert client.post("/api/outline", json={"text": "x" * 2_000_001}).status_code == 413
