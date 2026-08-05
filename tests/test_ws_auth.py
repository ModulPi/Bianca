import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from agent.config import Settings, clear_settings_cache, set_effective_settings
from agent.main import app


def test_ws_rejects_without_token_when_auth_enabled():
    clear_settings_cache()
    set_effective_settings(Settings(api_token="secret-token"))
    try:
        client = TestClient(app)
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect("/api/v1/ws/system") as ws:
                ws.receive_text()
        assert exc.value.code == 1008
    finally:
        clear_settings_cache()


def test_ws_accepts_query_token_when_auth_enabled():
    clear_settings_cache()
    set_effective_settings(Settings(api_token="secret-token"))
    try:
        client = TestClient(app)
        with client.websocket_connect("/api/v1/ws/system?token=secret-token") as ws:
            ws.send_text("ping")
    finally:
        clear_settings_cache()


def test_ws_open_without_auth_when_token_unset():
    clear_settings_cache()
    set_effective_settings(Settings(api_token=""))
    try:
        client = TestClient(app)
        with client.websocket_connect("/api/v1/ws/system") as ws:
            ws.send_text("ping")
    finally:
        clear_settings_cache()
