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
    for key in ["j", "k", "c", "x", "p", "m", "t", "d"]:
        assert f'key === "{key}"' in app_js
    assert "location.reload()" in app_js
    assert "localStorage" in app_js
    assert 'action = status === "running" ? "pause"' in app_js
    assert "drawSparkline" in app_js and "showUsage" in app_js
    assert "renderPreflight" in app_js  # FR-05: pre-flight questions shown on demand
    assert "renderResult" in app_js and "/api/final/" in app_js  # exports shown in the UI
    assert "setDense" in app_js and "glance(" in app_js  # compact live surface (D22)
    assert "set_language" in app_js  # the language can be locked from the top bar
    css = (UI_DIR / "styles.css").read_text(encoding="utf-8")
    assert "--font: 18px" in css
    for state in ["warm", "touched", "covered", "skipped", "hot", "pinned", "current", "selected"]:
        assert f".node.{state}" in css
    html = (UI_DIR / "index.html").read_text(encoding="utf-8")
    assert json.dumps("edge-up")[1:-1] in html
    assert "sparkline" in html and "usage-body" in html
    assert "lang-status" in html and "btn-density" in html and "result-body" in html


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
