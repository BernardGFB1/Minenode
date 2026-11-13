"""Shared MineNodeAuth account and session file used by the TCP proxy and Paper lobby.

The file is a simple tab-separated database with one row per registered player:

``name    salt    password_hash    last_ip    expires_at``

A row exists for every registered player. ``expires_at`` is the end of the
current 30-minute login session; a zero value means the player is registered
but must log in again.  Passwords are never exposed through the panel API.

The Paper plugin writes the same format, so the file must stay compatible with
``MineNodeAuth.java``.  Timestamps are integer Unix seconds.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
import time

from .config import DATA_DIR

SESSION_FILE = DATA_DIR / "auth" / "sessions.db"
PENDING_FILE = DATA_DIR / "auth" / "pending.db"
FINGERPRINT_FILE = DATA_DIR / "auth" / "fingerprints.json"
SESSION_TTL = 30 * 60
PENDING_TTL = 30 * 60
FINGERPRINT_TTL = 7 * 24 * 60 * 60
FINGERPRINT_LIMIT = 1000
PASSWORD_ITERATIONS = 120_000
PASSWORD_MIN_LENGTH = 6
_lock = threading.RLock()


def _read() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    try:
        for line in SESSION_FILE.read_text(encoding="utf-8").splitlines():
            parts = line.split("\t")
            if len(parts) == 5 and parts[0]:
                out[parts[0].lower()] = parts
    except FileNotFoundError:
        pass
    return out


def _write(data: dict[str, list[str]]) -> None:
    SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = SESSION_FILE.with_name(SESSION_FILE.name + ".tmp")
    text = "\n".join("\t".join(v) for v in data.values()) + ("\n" if data else "")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, SESSION_FILE)


def _expiry(row: list[str]) -> float:
    try:
        return float(row[4])
    except (ValueError, IndexError):
        return 0.0


def _norm_ip(ip: str) -> str:
    """Treat IPv4 and its IPv4-mapped IPv6 form as the same client."""
    ip = (ip or "").strip()
    if ip.lower().startswith("::ffff:") and "." in ip:
        return ip[7:]
    return ip


def _pending_read() -> dict[str, tuple[str, int]]:
    """Return ``name -> (ip, timestamp)`` for recent pending lobby entries."""
    out: dict[str, tuple[str, int]] = {}
    try:
        for line in PENDING_FILE.read_text(encoding="utf-8").splitlines():
            parts = line.split("\t")
            if len(parts) != 3 or not parts[0]:
                continue
            try:
                ts = int(float(parts[2]))
            except ValueError:
                continue
            if time.time() - ts < PENDING_TTL:
                out[parts[0].lower()] = (parts[1], ts)
    except FileNotFoundError:
        pass
    return out


def _pending_write(rows: dict[str, tuple[str, int]]) -> None:
    PENDING_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = PENDING_FILE.with_name(PENDING_FILE.name + ".tmp")
    text = "\n".join(f"{name}\t{ip}\t{ts}" for name, (ip, ts) in rows.items())
    tmp.write_text(text + ("\n" if text else ""), encoding="utf-8", newline="\n")
    os.replace(tmp, PENDING_FILE)


def valid(name: str, ip: str) -> bool:
    with _lock:
        row = _read().get(name.lower())
        if not row or _norm_ip(row[3]) != _norm_ip(ip):
            return False
        return _expiry(row) > time.time()


def touch(name: str, ip: str) -> bool:
    """Refresh a matching session for another 30 minutes."""
    with _lock:
        data = _read()
        row = data.get(name.lower())
        if not row or _norm_ip(row[3]) != _norm_ip(ip) or _expiry(row) <= time.time():
            return False
        row[4] = str(int(time.time()) + SESSION_TTL)
        _write(data)
        return True


def set_pending(name: str, ip: str) -> None:
    """Pass the real client IP to the local Paper lobby (proxy is its peer)."""
    with _lock:
        rows = _pending_read()
        rows[name.lower()] = (ip, int(time.time()))
        _pending_write(rows)


def _matches(name: str, ip: str, query: str) -> bool:
    q = query.strip().lower()
    return not q or q in name.lower() or q in ip.lower()


def sessions(query: str = "") -> list[dict]:
    """Currently active player sessions, newest expiry first. Never exposes hashes."""
    now = time.time()
    with _lock:
        rows = _read()
        fingerprints = _fingerprints_read()
    out = []
    for row in rows.values():
        if _expiry(row) <= now or not _matches(row[0], row[3], query):
            continue
        entry = {"name": row[0], "ip": row[3], "expires_at": _expiry(row)}
        entry["fingerprint"] = _fingerprint_summary(fingerprints.get(row[0].lower()), row[3])
        out.append(entry)
    out.sort(key=lambda item: item["expires_at"], reverse=True)
    return out


def accounts(query: str = "") -> list[dict]:
    """Every registered player together with session state. Never exposes hashes."""
    now = time.time()
    with _lock:
        rows = _read()
    out = [
        {"name": row[0], "ip": row[3], "expires_at": _expiry(row), "active": _expiry(row) > now}
        for row in rows.values()
        if _matches(row[0], row[3], query)
    ]
    out.sort(key=lambda item: item["name"].lower())
    return out


def clear_session(name: str) -> bool:
    """Log a player out while keeping their password for the next /login."""
    with _lock:
        data = _read()
        row = data.get(name.lower())
        if not row:
            return False
        row[4] = "0"
        _write(data)
        _fingerprint_delete(name)
        return True


def delete_account(name: str) -> bool:
    """Remove a password so the player can register again from scratch."""
    with _lock:
        data = _read()
        if data.pop(name.lower(), None) is None:
            return False
        _write(data)
        _fingerprint_delete(name)
        return True


def password_hash(password: str, salt: str) -> str:
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt),
                                 PASSWORD_ITERATIONS, dklen=32)
    return digest.hex()


def set_password(name: str, password: str) -> bool:
    """Set a new password and invalidate the player's current session."""
    if len(password) < PASSWORD_MIN_LENGTH:
        raise ValueError(f"Hasło musi mieć co najmniej {PASSWORD_MIN_LENGTH} znaków")
    if any(ch.isspace() for ch in password):
        raise ValueError("Hasło nie może zawierać spacji")
    with _lock:
        data = _read()
        row = data.get(name.lower())
        if not row:
            return False
        salt = secrets.token_hex(16)
        row[1] = salt
        row[2] = password_hash(password, salt)
        row[4] = "0"
        _write(data)
        _fingerprint_delete(name)
        return True


# ====================================================================== client fingerprints

_CHAT_MODES = {0: "full", 1: "commands", 2: "hidden"}
_MAIN_HANDS = {0: "right", 1: "left"}


def _fingerprints_read() -> dict[str, dict]:
    try:
        data = json.loads(FINGERPRINT_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {key: value for key, value in data.items() if isinstance(value, dict)}


def _fingerprints_write(data: dict[str, dict]) -> None:
    FINGERPRINT_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = FINGERPRINT_FILE.with_name(FINGERPRINT_FILE.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                   encoding="utf-8", newline="\n")
    os.replace(tmp, FINGERPRINT_FILE)


def _fingerprint_hash(protocol: int | None, raw: dict | None, brand: str | None,
                      channels: list[str]) -> str:
    canonical = {
        "protocol": protocol,
        "client_info": None if not raw else {
            key: raw.get(key)
            for key in ("locale", "view_distance", "chat_mode", "chat_colors", "skin_parts",
                        "main_hand", "text_filtering", "allow_listings", "particle_status")
        },
        "brand": brand or "",
        "channels": sorted(set(channels)),
    }
    payload = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _client_info_view(raw: dict) -> dict:
    return {
        "locale": raw.get("locale"),
        "view_distance": raw.get("view_distance"),
        "chat_mode": _CHAT_MODES.get(raw.get("chat_mode"), raw.get("chat_mode")),
        "chat_colors": raw.get("chat_colors"),
        "skin_parts": raw.get("skin_parts"),
        "skin_parts_enabled": raw.get("skin_parts_enabled") or [],
        "main_hand": _MAIN_HANDS.get(raw.get("main_hand"), raw.get("main_hand")),
        "text_filtering": raw.get("text_filtering"),
        "allow_listings": raw.get("allow_listings"),
        "particle_status": raw.get("particle_status"),
    }


def _fingerprint_summary(entry: dict | None, ip: str) -> dict | None:
    if not entry or _norm_ip(entry.get("ip", "")) != _norm_ip(ip):
        return None
    return {
        "hash": entry.get("hash"),
        "updated_at": entry.get("updated_at"),
        "brand": entry.get("brand"),
        "channels": len(entry.get("plugin_messages") or []),
        "has_client_info": bool(entry.get("client_info")),
    }


def _fingerprint_public(entry: dict) -> dict:
    return {
        "hash": entry.get("hash"),
        "protocol": entry.get("protocol"),
        "updated_at": entry.get("updated_at"),
        "captured_at": entry.get("captured_at"),
        "frames": entry.get("frames", 0),
        "client_info": entry.get("client_info"),
        "brand": entry.get("brand"),
        "plugin_messages": entry.get("plugin_messages") or [],
    }


def record_fingerprint(name: str, ip: str, protocol: int, snapshot: dict) -> None:
    """Merge one proxy capture into the player's current session fingerprint.

    Entries are keyed by player name and only shown while the stored IP matches
    the active session.  Password material is never involved.
    """
    if not name or not snapshot:
        return
    now = int(time.time())
    with _lock:
        data = _fingerprints_read()
        key = name.lower()
        old = data.get(key)
        if (not old or now - int(old.get("updated_at", 0)) > FINGERPRINT_TTL
                or _norm_ip(old.get("ip", "")) != _norm_ip(ip)):
            old = {}

        raw = snapshot.get("client_info") or old.get("client_info_raw")
        brand = snapshot.get("brand") or old.get("brand")
        messages: dict[str, dict] = {}
        for message in old.get("plugin_messages") or []:
            channel = message.get("channel")
            if channel:
                messages[channel] = dict(message)
        for message in snapshot.get("plugin_messages") or []:
            channel = message.get("channel")
            if not channel:
                continue
            current = messages.get(channel)
            if current:
                current["count"] = max(int(current.get("count", 0)), int(message.get("count", 0)))
                current["bytes"] = max(int(current.get("bytes", 0)), int(message.get("bytes", 0)))
                current["sha256"] = message.get("sha256") or current.get("sha256")
            else:
                messages[channel] = dict(message)

        if not raw and not messages:
            return

        entry = {
            "name": name,
            "ip": ip,
            "protocol": protocol,
            "hash": _fingerprint_hash(protocol, raw, brand, list(messages)),
            "client_info_raw": raw,
            "client_info": _client_info_view(raw) if raw else None,
            "brand": brand,
            "plugin_messages": [messages[channel] for channel in sorted(messages, key=str.lower)],
            "frames": max(int(old.get("frames", 0)), int(snapshot.get("frames", 0))),
            "captured_at": snapshot.get("captured_at") or old.get("captured_at") or now,
            "updated_at": now,
        }
        data[key] = entry
        for old_key, old_entry in list(data.items()):
            if now - int(old_entry.get("updated_at", 0)) > FINGERPRINT_TTL:
                data.pop(old_key, None)
        if len(data) > FINGERPRINT_LIMIT:
            keep = sorted(data.items(), key=lambda item: int(item[1].get("updated_at", 0)),
                          reverse=True)[:FINGERPRINT_LIMIT]
            data = dict(keep)
        _fingerprints_write(data)


def fingerprint(name: str, ip: str | None = None) -> dict | None:
    """Full captured fingerprint for one player, optionally bound to an IP."""
    with _lock:
        entry = _fingerprints_read().get(name.lower())
    if not entry:
        return None
    if ip is not None and _norm_ip(entry.get("ip", "")) != _norm_ip(ip):
        return None
    return _fingerprint_public(entry)


def _fingerprint_delete(name: str) -> None:
    with _lock:
        data = _fingerprints_read()
        if data.pop(name.lower(), None) is None:
            return
        _fingerprints_write(data)
