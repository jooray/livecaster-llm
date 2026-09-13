"""A server bound past loopback needs a key (FR-42).

The default 127.0.0.1 bind must behave exactly as it always did, so half of these
assert that nothing happens.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from livecaster.engine import Engine
from livecaster.llm.mock import MockLLM
from livecaster.server.app import create_app
from livecaster.server.auth import (
    COOKIE_NAME,
    HEADER_NAME,
    client_url,
    is_loopback,
    needs_token,
    new_token,
    token_ok,
)


@pytest.fixture
def engine(store, config, fixtures):
    from livecaster.timeutil import ManualClock

    return Engine(store, config, MockLLM(fixtures / "tick_responses"), clock=ManualClock())


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1", "127.0.0.5"])
def test_loopback_binds_need_no_token(host):
    assert is_loopback(host)
    assert not needs_token(host)


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.10", "::", "10.0.0.2", "example.local"])
def test_routable_binds_need_a_token(host):
    assert needs_token(host)


def test_tokens_are_not_guessable_and_compare_exactly():
    a, b = new_token(), new_token()
    assert a != b
    assert len(a) >= 20
    assert token_ok(a, a)
    assert not token_ok(a, b)
    assert not token_ok(a, None)
    assert not token_ok(a, "")
    assert not token_ok(a, a[:-1])


def test_client_url_carries_the_token():
    url = client_url("192.168.1.10", 8766, "abc123")
    assert url == "http://192.168.1.10:8766/?k=abc123"


def test_client_url_without_a_token_is_plain():
    assert client_url("192.168.1.10", 8766, None) == "http://192.168.1.10:8766/"


# --- through the app --------------------------------------------------------


def test_no_token_configured_leaves_every_route_open(engine):
    """The default local server: unchanged, and the middleware is not even installed."""
    with TestClient(create_app(engine)) as client:
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/state").status_code == 200
        assert client.get("/").status_code == 200


def test_loopback_client_is_exempt_even_when_a_token_exists(engine):
    """The host's own browser reaches a 0.0.0.0 bind over loopback: --open, start.sh."""
    with TestClient(create_app(engine, token="secret"), client=("127.0.0.1", 9999)) as client:
        assert client.get("/api/state").status_code == 200


def test_off_machine_request_without_a_key_is_refused(engine):
    with TestClient(create_app(engine, token="secret"), client=("192.168.1.50", 9999)) as client:
        assert client.get("/api/state").status_code == 401
        assert client.get("/api/control").status_code == 401


def test_off_machine_request_with_the_key_is_allowed(engine):
    with TestClient(create_app(engine, token="secret"), client=("192.168.1.50", 9999)) as client:
        assert client.get("/api/state", params={"k": "secret"}).status_code == 200
        assert client.get("/api/state", headers={HEADER_NAME: "secret"}).status_code == 200


def test_a_wrong_key_is_refused(engine):
    with TestClient(create_app(engine, token="secret"), client=("192.168.1.50", 9999)) as client:
        assert client.get("/api/state", params={"k": "nope"}).status_code == 401


def test_the_key_becomes_a_cookie_so_static_and_api_follow(engine):
    with TestClient(create_app(engine, token="secret"), client=("192.168.1.50", 9999)) as client:
        assert client.get("/", params={"k": "secret"}).status_code == 200
        assert client.cookies.get(COOKIE_NAME) == "secret"
        # No query string this time: the cookie carries it.
        assert client.get("/api/state").status_code == 200


def test_health_stays_open_for_uptime_probes(engine):
    with TestClient(create_app(engine, token="secret"), client=("192.168.1.50", 9999)) as client:
        assert client.get("/api/health").status_code == 200


def test_websocket_off_machine_needs_the_key(engine):
    app = create_app(engine, token="secret")
    with TestClient(app, client=("192.168.1.50", 9999)) as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws"):
                pass
        with client.websocket_connect("/ws?k=secret") as ws:
            assert ws.receive_json()["type"] == "hello"


def test_websocket_over_loopback_is_unchanged(engine):
    with TestClient(create_app(engine, token="secret"), client=("127.0.0.1", 9999)) as client:
        with client.websocket_connect("/ws") as ws:
            assert ws.receive_json()["type"] == "hello"
