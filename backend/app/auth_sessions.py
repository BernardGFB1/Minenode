"""Shared MineNodeAuth session file used by the TCP proxy and Paper lobby."""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path

from .config import DATA_DIR

SESSION_FILE = DATA_DIR / "auth" / "sessions.db"
PENDING_FILE = DATA_DIR / "auth" / "pending.db"
SESSION_TTL = 30 * 60
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


def valid(name: str, ip: str) -> bool:
    with _lock:
        row = _read().get(name.lower())
        if not row or row[3] != ip:
            return False
        try:
            return float(row[4]) > time.time()
        except ValueError:
            return False


def touch(name: str, ip: str) -> bool:
    """Refresh a matching session for another 30 minutes."""
    with _lock:
        data = _read()
        row = data.get(name.lower())
        if not row or row[3] != ip:
            return False
        try:
            if float(row[4]) <= time.time():
                return False
        except ValueError:
            return False
        row[4] = str(time.time() + SESSION_TTL)
        _write(data)
        return True


def set_pending(name: str, ip: str) -> None:
    """Pass the real client IP to the local Paper lobby (proxy is its peer)."""
    with _lock:
        PENDING_FILE.parent.mkdir(parents=True, exist_ok=True)
        rows: dict[str, str] = {}
        try:
            for line in PENDING_FILE.read_text(encoding="utf-8").splitlines():
                n, old_ip, ts = line.split("\t")
                if time.time() - float(ts) < 120:
                    rows[n] = f"{old_ip}\t{ts}"
        except (FileNotFoundError, ValueError):
            pass
        rows[name.lower()] = f"{ip}\t{time.time()}"
        tmp = PENDING_FILE.with_name(PENDING_FILE.name + ".tmp")
        tmp.write_text("\n".join(f"{n}\t{v}" for n, v in rows.items()) + "\n", encoding="utf-8")
        os.replace(tmp, PENDING_FILE)
