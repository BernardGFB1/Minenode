"""Tests for game-account authentication storage and panel management endpoints."""
import time

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from app import api, auth, auth_sessions
from app.auth_sessions import password_hash
from app.store import JsonStore

PASSWORD = "correct-horse-battery"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(auth_sessions, "SESSION_FILE", tmp_path / "auth" / "sessions.db")
    monkeypatch.setattr(auth_sessions, "PENDING_FILE", tmp_path / "auth" / "pending.db")
    monkeypatch.setattr(auth_sessions, "FINGERPRINT_FILE", tmp_path / "auth" / "fingerprints.json")
    monkeypatch.setattr(auth, "_users", JsonStore(tmp_path / "users.json", dict))
    monkeypatch.setattr(auth, "_secret_file", tmp_path / "secret.key")
    monkeypatch.setattr(auth, "_attempts", {})
    auth.create_user("admin", "panel-password-123")


def put_row(name="Steve", ip="203.0.113.7", ttl=auth_sessions.SESSION_TTL, password="secret-password"):
    salt = "00" * 16
    row = [name, salt, password_hash(password, salt), ip, str(int(time.time()) + ttl)]
    auth_sessions.SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    with auth_sessions.SESSION_FILE.open("a", encoding="utf-8") as fh:
        fh.write("\t".join(row) + "\n")
    return row


def test_pending_file_uses_integer_timestamp_compatible_with_the_plugin(tmp_path):
    auth_sessions.set_pending("Steve", "203.0.113.7")
    line = auth_sessions.PENDING_FILE.read_text(encoding="utf-8").strip()
    name, ip, ts = line.split("\t")
    assert (name, ip) == ("steve", "203.0.113.7")
    int(ts)  # the old float value made the Java plugin fall back to 127.0.0.1


def test_session_is_bound_to_name_and_ip_and_touch_extends_it():
    put_row()
    assert auth_sessions.valid("steve", "203.0.113.7")
    assert auth_sessions.valid("Steve", "203.0.113.7")
    assert auth_sessions.valid("Steve", "::ffff:203.0.113.7")
    assert not auth_sessions.valid("steve", "203.0.113.8")
    before = auth_sessions.sessions()[0]["expires_at"]
    assert auth_sessions.touch("steve", "203.0.113.7")
    after = auth_sessions.sessions()[0]["expires_at"]
    assert after >= before
    assert not auth_sessions.touch("steve", "203.0.113.8")


def test_clearing_a_session_keeps_the_password():
    put_row()
    assert auth_sessions.clear_session("steve")
    assert not auth_sessions.valid("steve", "203.0.113.7")
    assert auth_sessions.sessions() == []
    [account] = auth_sessions.accounts()
    assert account == {"name": "Steve", "ip": "203.0.113.7", "expires_at": 0.0, "active": False}
    # the account still exists, so /login remains possible
    assert auth_sessions.set_password("steve", "new-secret-password")


def test_password_change_hashes_with_the_plugin_algorithm_and_logs_out():
    put_row()
    assert auth_sessions.set_password("steve", "new-secret-password")
    [account] = auth_sessions.accounts()
    assert account["active"] is False
    row = auth_sessions.SESSION_FILE.read_text(encoding="utf-8").strip().split("\t")
    assert row[2] == password_hash("new-secret-password", row[1])
    assert "new-secret-password" not in auth_sessions.SESSION_FILE.read_text(encoding="utf-8")
    with pytest.raises(ValueError):
        auth_sessions.set_password("steve", "short")
    with pytest.raises(ValueError):
        auth_sessions.set_password("steve", "has a space")


def test_deleting_the_password_removes_the_account_and_allows_registration():
    put_row()
    assert auth_sessions.delete_account("STEVE")
    assert auth_sessions.accounts() == []
    assert not auth_sessions.delete_account("steve")


def test_search_filters_sessions_and_accounts():
    put_row("Steve", "203.0.113.7")
    put_row("Alex", "198.51.100.9")
    assert [s["name"] for s in auth_sessions.sessions("stev")] == ["Steve"]
    assert [s["name"] for s in auth_sessions.sessions("198.51")] == ["Alex"]
    assert {a["name"] for a in auth_sessions.accounts("203.0")} == {"Steve"}


def test_panel_endpoints_never_expose_hashes_and_manage_access():
    put_row()
    app = FastAPI()
    app.include_router(api.router)
    client = TestClient(app)
    client.cookies.set(auth.COOKIE, auth.make_token("admin"))

    sessions = client.get("/api/player-auth/sessions").json()
    accounts = client.get("/api/player-auth/accounts").json()
    assert sessions[0]["name"] == "Steve"
    assert accounts[0]["name"] == "Steve" and accounts[0]["active"] is True
    for payload in (sessions, accounts):
        text = str(payload)
        assert "salt" not in text and "hash" not in text and PASSWORD not in text

    assert client.delete("/api/player-auth/sessions/steve").status_code == 200
    assert client.get("/api/player-auth/sessions").json() == []
    assert client.get("/api/player-auth/accounts").json()[0]["active"] is False

    assert client.put("/api/player-auth/accounts/steve/password", json={"password": "brand-new-password"}).status_code == 200
    assert client.delete("/api/player-auth/accounts/steve").status_code == 200
    assert client.delete("/api/player-auth/accounts/steve").status_code == 404


def fingerprint_snapshot(locale="en_us", channel="minecraft:brand"):
    return {
        "client_info": {
            "locale": locale,
            "view_distance": 12,
            "chat_mode": 0,
            "chat_colors": True,
            "skin_parts": 0x7F,
            "skin_parts_enabled": ["cape", "hat"],
            "main_hand": 0,
            "text_filtering": False,
            "allow_listings": True,
            "particle_status": None,
        },
        "plugin_messages": [{"channel": channel, "count": 1, "bytes": 8, "sha256": "ab" * 32}],
        "brand": "vanilla",
        "frames": 4,
        "captured_at": int(time.time()),
    }


def test_fingerprint_is_stable_merged_and_bound_to_the_session_ip():
    put_row()
    auth_sessions.record_fingerprint("Steve", "203.0.113.7", 769, fingerprint_snapshot())
    first = auth_sessions.fingerprint("steve", "203.0.113.7")
    assert first["hash"] and first["brand"] == "vanilla"
    assert first["client_info"]["chat_mode"] == "full"
    assert first["client_info"]["main_hand"] == "right"

    auth_sessions.record_fingerprint(
        "STEVE", "203.0.113.7", 769,
        {**fingerprint_snapshot(), "plugin_messages": [
            {"channel": "fabric:registry/sync", "count": 2, "bytes": 10, "sha256": "cd" * 32},
        ]},
    )
    merged = auth_sessions.fingerprint("steve", "203.0.113.7")
    assert merged["hash"] != first["hash"]  # channel names are part of the hash
    assert {m["channel"] for m in merged["plugin_messages"]} == {"minecraft:brand", "fabric:registry/sync"}
    assert auth_sessions.fingerprint("steve", "198.51.100.9") is None

    [session] = auth_sessions.sessions()
    assert session["fingerprint"]["hash"] == merged["hash"]
    assert session["fingerprint"]["channels"] == 2


def test_session_detail_endpoint_returns_fingerprint_but_never_password_material():
    put_row()
    auth_sessions.record_fingerprint("Steve", "203.0.113.7", 769, fingerprint_snapshot())
    app = FastAPI()
    app.include_router(api.router)
    client = TestClient(app)
    client.cookies.set(auth.COOKIE, auth.make_token("admin"))

    detail = client.get("/api/player-auth/sessions/steve")
    assert detail.status_code == 200
    body = detail.json()
    assert body["session"]["name"] == "Steve"
    assert body["fingerprint"]["hash"]
    text = str(body)
    assert "salt" not in text and "password" not in text.lower() and PASSWORD not in text
    assert client.get("/api/player-auth/sessions/nobody").status_code == 404


def test_logout_password_change_and_account_removal_clear_the_fingerprint():
    put_row()
    auth_sessions.record_fingerprint("Steve", "203.0.113.7", 769, fingerprint_snapshot())
    assert auth_sessions.fingerprint("steve")
    auth_sessions.clear_session("steve")
    assert auth_sessions.fingerprint("steve") is None

    put_row()
    auth_sessions.record_fingerprint("Steve", "203.0.113.7", 769, fingerprint_snapshot())
    auth_sessions.set_password("steve", "new-secret-password")
    assert auth_sessions.fingerprint("steve") is None

    put_row()
    auth_sessions.record_fingerprint("Steve", "203.0.113.7", 769, fingerprint_snapshot())
    auth_sessions.delete_account("steve")
    assert auth_sessions.fingerprint("steve") is None
