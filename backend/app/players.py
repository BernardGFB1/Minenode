"""Player list files (whitelist / ops / bans) and UUID resolution."""
from __future__ import annotations

import hashlib
import json
import re
import time
import uuid as uuidlib
from pathlib import Path

import httpx

from . import net
from .store import atomic_write_text

NAME_RE = re.compile(r"^[A-Za-z0-9_]{1,16}$")
_uuid_cache: dict[str, tuple[float, dict | None]] = {}


def offline_uuid(name: str) -> str:
    digest = hashlib.md5(f"OfflinePlayer:{name}".encode()).digest()
    return str(uuidlib.UUID(bytes=digest, version=3))


async def lookup_profile(name: str, online_mode: bool) -> dict:
    """Return {'uuid', 'name'} for a player. Offline servers use deterministic offline UUIDs."""
    if not NAME_RE.match(name):
        raise ValueError("Niepoprawny nick gracza")
    if not online_mode:
        return {"uuid": offline_uuid(name), "name": name}
    key = name.lower()
    hit = _uuid_cache.get(key)
    if hit and time.time() - hit[0] < 3600 and hit[1]:
        return hit[1]
    try:
        data = await net.get_json(f"https://api.mojang.com/users/profiles/minecraft/{name}")
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in (204, 404):
            raise ValueError(f"Konto Minecraft „{name}” nie istnieje") from exc
        raise
    except ValueError as exc:  # empty body (204)
        raise ValueError(f"Konto Minecraft „{name}” nie istnieje") from exc
    raw = data["id"]
    prof = {"uuid": str(uuidlib.UUID(raw)), "name": data["name"]}
    _uuid_cache[key] = (time.time(), prof)
    return prof


def read_list(path: Path) -> list[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def write_list(path: Path, items: list[dict]) -> None:
    atomic_write_text(path, json.dumps(items, indent=2, ensure_ascii=False))


def contains(items: list[dict], name: str) -> bool:
    n = name.lower()
    return any(str(i.get("name", "")).lower() == n for i in items)


def add_whitelist(path: Path, profile: dict) -> None:
    items = read_list(path)
    if not contains(items, profile["name"]):
        items.append({"uuid": profile["uuid"], "name": profile["name"]})
        write_list(path, items)


def add_op(path: Path, profile: dict, level: int = 4, bypass: bool = False) -> None:
    items = [i for i in read_list(path) if str(i.get("name", "")).lower() != profile["name"].lower()]
    items.append({"uuid": profile["uuid"], "name": profile["name"], "level": int(level),
                  "bypassesPlayerLimit": bool(bypass)})
    write_list(path, items)


def remove_by_name(path: Path, name: str) -> bool:
    items = read_list(path)
    keep = [i for i in items if str(i.get("name", "")).lower() != name.lower()]
    if len(keep) != len(items):
        write_list(path, keep)
        return True
    return False
