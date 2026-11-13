import asyncio
import concurrent.futures as cf
from collections import deque

import pytest
from fastapi import FastAPI, HTTPException
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app import api, auth
from app.store import JsonStore

PASSWORD = "correct-horse-battery"


@pytest.fixture(autouse=True)
def fresh_auth(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "_users", JsonStore(tmp_path / "users.json", dict))
    monkeypatch.setattr(auth, "_secret_file", tmp_path / "secret.key")
    monkeypatch.setattr(auth, "_attempts", {})
    auth.create_user("admin", PASSWORD)


def _try(username, password, ip):
    try:
        auth.login(username, password, ip)
        return 200
    except HTTPException as exc:
        return exc.status_code


def test_parallel_attempts_cannot_exceed_the_ip_limit():
    with cf.ThreadPoolExecutor(40) as ex:
        codes = list(ex.map(lambda _: _try("admin", "wrong-password", "203.0.113.9"), range(40)))
    assert codes.count(401) == auth.IP_LIMIT
    assert codes.count(429) == 40 - auth.IP_LIMIT


def test_account_limit_holds_across_many_ips():
    codes = [_try("admin", "wrong-password", f"198.51.100.{i}") for i in range(auth.ACCOUNT_LIMIT + 5)]
    assert codes.count(401) == auth.ACCOUNT_LIMIT
    assert codes[-1] == 429
    assert _try("other-user", "whatever-pass", "198.51.100.250") == 401  # other accounts unaffected


def test_successful_login_clears_the_counters():
    for _ in range(auth.IP_LIMIT - 1):
        assert _try("admin", "wrong-password", "203.0.113.10") == 401
    assert _try("admin", PASSWORD, "203.0.113.10") == 200
    assert _try("admin", "wrong-password", "203.0.113.10") == 401  # fresh window, not 429


def test_unknown_user_still_runs_the_password_hash(monkeypatch):
    calls = []
    real = auth.verify_password
    monkeypatch.setattr(auth, "verify_password", lambda p, h: calls.append(h) or real(p, h))
    assert _try("nobody", "some-password", "203.0.113.11") == 401
    assert _try("admin", "some-password", "203.0.113.12") == 401
    assert len(calls) == 2 and all(h.startswith("scrypt$") for h in calls)


def test_tracked_keys_are_bounded(monkeypatch):
    monkeypatch.setattr(auth, "_MAX_TRACKED", 50)
    for i in range(200):
        auth.check_rate_limit(f"10.0.{i // 250}.{i % 250}")
    assert len(auth._attempts) <= 50


# ---------------------------------------------------------------- WebSocket console session

class FakeInstance:
    def __init__(self):
        self.commands = []
        self.console, self.stats, self.job = deque(), deque(), None

    def subscribe(self):
        return asyncio.Queue()

    def unsubscribe(self, q):
        pass

    def summary(self):
        return {"id": "srv"}

    async def send_command(self, cmd):
        self.commands.append(cmd)


@pytest.fixture
def ws_client(monkeypatch):
    inst = FakeInstance()
    monkeypatch.setattr(api, "manager", type("M", (), {"instances": {"srv": inst}})(), raising=False)
    app = FastAPI()
    app.include_router(api.router)
    client = TestClient(app)
    client.cookies.set(auth.COOKIE, auth.make_token("admin"))
    return client, inst


def test_console_command_works_with_a_valid_session(ws_client):
    client, inst = ws_client
    with client.websocket_connect("/api/servers/srv/ws") as ws:
        assert ws.receive_json()["type"] == "init"
        ws.send_json({"type": "command", "command": "list"})
        ws.send_json({"type": "ping"})  # a second message proves the first one was handled
    assert inst.commands == ["list"]


def test_console_command_after_password_change_is_refused(ws_client):
    client, inst = ws_client
    with client.websocket_connect("/api/servers/srv/ws") as ws:
        assert ws.receive_json()["type"] == "init"
        auth.set_password("admin", "a-new-password-123")  # invalidates every existing session
        ws.send_json({"type": "command", "command": "stop"})
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
    assert closed.value.code == 4401
    assert inst.commands == []


def test_idle_console_is_closed_when_the_account_is_deleted(ws_client, monkeypatch):
    monkeypatch.setattr(api, "WS_SESSION_CHECK", 0.05)
    auth.create_user("second", "another-password")
    client, inst = ws_client
    with client.websocket_connect("/api/servers/srv/ws") as ws:
        assert ws.receive_json()["type"] == "init"
        auth.delete_user("admin")
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
    assert closed.value.code == 4401
