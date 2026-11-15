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


def test_extended_session_row_is_readable_and_password_material_is_preserved():
    row = put_row()
    row.append("stable-client-hash")
    auth_sessions.SESSION_FILE.write_text("\t".join(row) + "\n", encoding="utf-8")
    before = auth_sessions.SESSION_FILE.read_text(encoding="utf-8").split("\t")

    assert auth_sessions.valid("Steve", "203.0.113.7")
    assert auth_sessions.touch("Steve", "203.0.113.7")
    after_touch = auth_sessions.SESSION_FILE.read_text(encoding="utf-8").split("\t")
    assert after_touch[1:3] == before[1:3]
    assert after_touch[5].strip() == "stable-client-hash"

    assert auth_sessions.set_password("Steve", "new-secret-password")
    after_password = auth_sessions.SESSION_FILE.read_text(encoding="utf-8").split("\t")
    assert after_password[1] != before[1]
    assert after_password[2] == password_hash("new-secret-password", after_password[1])
    assert after_password[5].strip() == ""


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

    # A stable fingerprint change invalidates the session and requires login.
    assert auth_sessions.sessions() == []


def test_fingerprint_hash_ignores_variable_client_settings():
    put_row()
    first = fingerprint_snapshot()
    auth_sessions.record_fingerprint("Steve", "203.0.113.7", 769, first)
    original = auth_sessions.fingerprint("Steve")["hash"]

    changed = fingerprint_snapshot()
    changed["client_info"] = {
        **changed["client_info"],
        "locale": "pl_pl", "view_distance": 32, "chat_mode": 2,
        "chat_colors": False, "skin_parts": 0, "main_hand": 1,
        "text_filtering": True, "allow_listings": False,
    }
    auth_sessions.record_fingerprint("Steve", "203.0.113.7", 769, changed)
    assert auth_sessions.fingerprint("Steve")["hash"] == original
    assert auth_sessions.valid("Steve", "203.0.113.7")


def test_changed_stable_fingerprint_invalidates_session_but_keeps_password():
    row = put_row()
    auth_sessions.record_fingerprint("Steve", "203.0.113.7", 769, fingerprint_snapshot())
    original_password_hash = row[2]
    changed = fingerprint_snapshot(channel="fabric:registry/sync")
    auth_sessions.record_fingerprint("Steve", "203.0.113.7", 769, changed)

    assert auth_sessions.sessions() == []
    assert not auth_sessions.valid("Steve", "203.0.113.7")
    stored = auth_sessions.SESSION_FILE.read_text(encoding="utf-8").split("\t")
    assert stored[2] == original_password_hash
    assert auth_sessions.set_password("Steve", "replacement-password")


def test_successful_login_rebinds_fingerprint_without_invalidating_the_new_session():
    row = put_row()
    auth_sessions.record_fingerprint("Steve", "203.0.113.7", 769, fingerprint_snapshot())
    assert auth_sessions.valid("Steve", "203.0.113.7")

    # Rust /login refreshes expiry and clears the old fingerprint binding.
    row[4] = str(int(time.time()) + auth_sessions.SESSION_TTL)
    row.append("")
    auth_sessions.SESSION_FILE.write_text("\t".join(row) + "\n", encoding="utf-8")

    # The first capture after login belongs to the new session and must be
    # accepted as its baseline, even when it differs from the pre-login capture.
    auth_sessions.record_fingerprint(
        "Steve", "203.0.113.7", 769, fingerprint_snapshot(channel="fabric:registry/sync"))
    assert auth_sessions.valid("Steve", "203.0.113.7")
    assert auth_sessions.sessions()[0]["name"] == "Steve"


def test_valid_does_not_adopt_a_capture_and_target_capture_fills_the_empty_column():
    put_row()
    auth_sessions.record_fingerprint("Steve", "203.0.113.7", 769, fingerprint_snapshot())
    expected = auth_sessions.fingerprint("Steve")["hash"]

    row = auth_sessions.SESSION_FILE.read_text(encoding="utf-8").strip().split("\t")
    assert len(row) == 6
    row[5] = ""
    auth_sessions.SESSION_FILE.write_text("\t".join(row) + "\n", encoding="utf-8")

    # Validating a session never binds the fingerprint itself: the stored entry
    # may belong to the lobby or to a previous client.
    assert auth_sessions.valid("Steve", "203.0.113.7")
    after_valid = auth_sessions.SESSION_FILE.read_text(encoding="utf-8").splitlines()[0].split("\t")
    assert len(after_valid) == 6
    assert after_valid[5] == ""

    # The first target-server capture establishes the binding in place.
    auth_sessions.record_fingerprint("Steve", "203.0.113.7", 769, fingerprint_snapshot())
    after_capture = auth_sessions.SESSION_FILE.read_text(encoding="utf-8").splitlines()[0].split("\t")
    assert len(after_capture) == 6
    assert after_capture[5] == expected


def test_lobby_capture_never_invalidates_or_binds_the_session():
    row = put_row()
    assert len(row) == 5

    # The hidden auth lobby is a different server: its plugin-message layout
    # must not invalidate the session that was just created there.
    auth_sessions.record_fingerprint(
        "Steve", "203.0.113.7", 769, fingerprint_snapshot(channel="minecraft:register"), bind=False)
    assert auth_sessions.valid("Steve", "203.0.113.7")
    stored = auth_sessions.SESSION_FILE.read_text(encoding="utf-8").strip().split("\t")
    assert len(stored) == 5

    # A later lobby capture with a different hash still leaves the session alone.
    auth_sessions.record_fingerprint(
        "Steve", "203.0.113.7", 769, fingerprint_snapshot(channel="lobby:hello"), bind=False)
    assert auth_sessions.valid("Steve", "203.0.113.7")
    assert auth_sessions.sessions()[0]["name"] == "Steve"


def test_lobby_capture_does_not_corrupt_a_bound_target_fingerprint():
    put_row()
    target = fingerprint_snapshot(channel="fabric:registry/sync")
    auth_sessions.record_fingerprint("Steve", "203.0.113.7", 769, target)
    bound = auth_sessions.fingerprint("Steve")["hash"]

    # The next visit starts in the lobby before the session is validated.
    # Its different channel set must neither change the stored hash nor
    # invalidate the session.
    auth_sessions.record_fingerprint(
        "Steve", "203.0.113.7", 769, fingerprint_snapshot(channel="lobby:hello"), bind=False)

    assert auth_sessions.fingerprint("Steve")["hash"] == bound
    assert auth_sessions.valid("Steve", "203.0.113.7")
    assert auth_sessions.sessions()[0]["name"] == "Steve"


def test_target_capture_establishes_the_session_binding_once():
    row = put_row()
    assert len(row) == 5

    target = fingerprint_snapshot(channel="fabric:registry/sync")
    auth_sessions.record_fingerprint("Steve", "203.0.113.7", 769, target)
    expected = auth_sessions.fingerprint("Steve")["hash"]
    stored = auth_sessions.SESSION_FILE.read_text(encoding="utf-8").strip().split("\t")
    assert len(stored) == 6
    assert stored[5] == expected
    assert auth_sessions.valid("Steve", "203.0.113.7")

    # A changed stable client on the target server must require a re-login.
    auth_sessions.record_fingerprint(
        "Steve", "203.0.113.7", 769, fingerprint_snapshot(channel="other:mod"))
    assert not auth_sessions.valid("Steve", "203.0.113.7")
    assert auth_sessions.sessions() == []


def test_changed_fingerprint_keeps_captured_details_available_to_the_panel():
    put_row()
    auth_sessions.record_fingerprint("Steve", "203.0.113.7", 769, fingerprint_snapshot())
    auth_sessions.record_fingerprint(
        "Steve", "203.0.113.7", 769, fingerprint_snapshot(channel="fabric:registry/sync"))

    assert not auth_sessions.valid("Steve", "203.0.113.7")
    captured = auth_sessions.fingerprint("Steve")
    assert captured is not None
    assert {m["channel"] for m in captured["plugin_messages"]} == {
        "minecraft:brand", "fabric:registry/sync"}


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


def test_registered_account_detail_is_available_after_logout_without_secrets():
    put_row()
    auth_sessions.record_fingerprint("Steve", "203.0.113.7", 769, fingerprint_snapshot())
    auth_sessions.clear_session("Steve")
    app = FastAPI()
    app.include_router(api.router)
    client = TestClient(app)
    client.cookies.set(auth.COOKIE, auth.make_token("admin"))

    response = client.get("/api/player-auth/accounts/steve")
    assert response.status_code == 200
    body = response.json()
    assert body["session"]["active"] is False
    assert body["fingerprint"]["hash"]
    text = str(body).lower()
    assert "salt" not in text and "password" not in text and PASSWORD not in text


def test_player_auth_endpoints_require_panel_authentication():
    app = FastAPI()
    app.include_router(api.router)
    client = TestClient(app)
    assert client.get("/api/player-auth/accounts").status_code in (401, 403)
    assert client.get("/api/player-auth/accounts/Steve").status_code in (401, 403)
    assert client.put("/api/player-auth/accounts/Steve/password", json={"password": "attacker-password"}).status_code in (401, 403)


def test_logout_and_password_change_keep_the_fingerprint_until_account_removal():
    put_row()
    auth_sessions.record_fingerprint("Steve", "203.0.113.7", 769, fingerprint_snapshot())
    assert auth_sessions.fingerprint("steve")
    auth_sessions.clear_session("steve")
    assert auth_sessions.fingerprint("steve") is not None

    put_row()
    auth_sessions.record_fingerprint("Steve", "203.0.113.7", 769, fingerprint_snapshot())
    auth_sessions.set_password("steve", "new-secret-password")
    assert auth_sessions.fingerprint("steve") is not None

    put_row()
    auth_sessions.record_fingerprint("Steve", "203.0.113.7", 769, fingerprint_snapshot())
    auth_sessions.delete_account("steve")
    assert auth_sessions.fingerprint("steve") is None


def test_bedrock_on_a_java_session_must_log_in_before_reaching_the_server():
    """Java logged in, then Bedrock from the same address: refused already at the handshake."""
    put_row()
    assert auth_sessions.record_fingerprint("Steve", "203.0.113.7", 769, fingerprint_snapshot(),
                                            edition="java") is False  # first capture binds the session
    assert auth_sessions.valid("Steve", "203.0.113.7", 769, "java")
    assert not auth_sessions.valid("Steve", "203.0.113.7", 769, "bedrock")
    assert auth_sessions.sessions() == []  # logged out, password kept
    assert not auth_sessions.valid("Steve", "203.0.113.7", 769, "java")


def test_another_game_version_on_a_bound_session_must_log_in():
    put_row()
    auth_sessions.record_fingerprint("Steve", "203.0.113.7", 769, fingerprint_snapshot(), edition="java")
    assert not auth_sessions.valid("Steve", "203.0.113.7", 774, "java")


def test_unbound_session_accepts_the_first_client_and_old_entries_without_edition():
    put_row()
    assert auth_sessions.valid("Steve", "203.0.113.7", 769, "bedrock")  # fresh /login: no binding yet
    auth_sessions.record_fingerprint("Steve", "203.0.113.7", 769, fingerprint_snapshot())  # no edition stored
    assert auth_sessions.valid("Steve", "203.0.113.7", 769, "bedrock")


def test_capture_reports_a_changed_client_so_the_proxy_can_disconnect_it():
    put_row()
    auth_sessions.record_fingerprint("Steve", "203.0.113.7", 769, fingerprint_snapshot(), edition="java")
    changed = fingerprint_snapshot(channel="fabric:registry/sync")
    assert auth_sessions.record_fingerprint("Steve", "203.0.113.7", 769, changed, edition="java") is True


def test_render_distance_is_deliberately_not_part_of_the_hash():
    a, b = fingerprint_snapshot(), fingerprint_snapshot()
    b["client_info"]["view_distance"] = 2
    put_row()
    auth_sessions.record_fingerprint("Steve", "203.0.113.7", 769, a)
    assert not auth_sessions.record_fingerprint("Steve", "203.0.113.7", 769, b)
    assert auth_sessions.valid("Steve", "203.0.113.7", 769)
