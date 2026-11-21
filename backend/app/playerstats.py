"""Per-player statistics derived from the server logs and world stats files.

Vanilla writes the death broadcast to the log, so deaths are counted from those lines.
Since 1.13 every engine also writes a per-player ``<uuid>.json`` into ``<level-name>/stats``;
that file is the source of the richer statistics shown on the public page.

Both the current ``latest.log`` and rotated logs are read; the current file is consumed
incrementally so refreshing the public page does not re-read the whole session.
"""
from __future__ import annotations

import gzip
import json
import re
import threading
import time
import uuid as uuidlib
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable

if TYPE_CHECKING:
    from .manager import ServerInstance

# "2026-11-19-3.log" / "2026-11-19-3.log.gz" – Minecraft's rotated logs. debug.log and
# other files in logs/ are deliberately ignored (they can repeat the same lines).
_ROTATED_LOG = re.compile(r"^\d{4}-\d{2}-\d{2}-\d+\.log(?:\.gz)?$", re.I)
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_MC_CODE = re.compile(r"§[0-9A-FK-ORa-fk-or]")
_SYSTEM_PREFIX = re.compile(r"^(?:\[Not Secure\]\s*|System chat:\s*)+")
_BRACKET_PREFIX = re.compile(r"^(?:\[[^\]]*\]\s*)+")

# Statistics files are named after the player's UUID, and "UUID of player X is ..." is logged
# on every join (premium and offline mode alike).
_UUID_FILE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_LOG_UUID = re.compile(r"UUID of player (\S+) is ([0-9a-fA-F-]{32,36})")
_VERSION = re.compile(r"^\s*(\d+)\.(\d+)")

# Vanilla names the victim first, e.g. "Steve was slain by Zombie", "Alex drowned".
_VICTIM = re.compile(r"^(?P<name>[^\s:]{1,40})\s+(?P<rest>\S.*)$")
_KILLED = (
    r"slain|shot|killed|blown\s+up|pricked|struck|impaled|fireballed|pummeled|squashed|"
    r"skewered|smashed|obliterated|doomed\s+to\s+fall|frozen|burnt|doused\s+in|poked|squished|"
    r"stung|spit\s+on|roasted|exploded|murdered|assassinated|eliminated"
)
_DEATH_REST = re.compile(
    r"^(?:"
    rf"(?:was|got|has\s+been)\s+(?:{_KILLED})\b"
    r"|has\s+died\b|died\b|drowned\b|fell\b"
    r"|hit\s+the\s+ground\s+too\s+hard\b|experienced\s+kinetic\s+energy\b"
    r"|burned\s+to\s+death\b|burnt\s+to\s+death\b|went\s+up\s+in\s+flames\b"
    r"|went\s+off\s+with\s+a\s+bang\b|starved\s+to\s+death\b|suffocated\s+in\s+a\s+wall\b"
    r"|withered\s+away\b|froze\s+to\s+death\b|tried\s+to\s+swim\s+in\s+lava\b"
    r"|walked\s+into\s+(?:fire|a\s+cactus|lava|danger\s+zone)\b"
    r"|discovered\s+the\s+floor\s+was\s+lava\b"
    r")",
    re.I,
)

# path -> parsed state (counts + read offset for latest.log)
_cache: dict[str, dict] = {}
_cache_lock = threading.Lock()


def _count_line(raw: bytes, counts: dict[str, int]) -> None:
    line = raw.decode("utf-8", "replace").rstrip("\r\n")
    if "]: " in line:  # strip the full log prefix, including "System chat:" variants
        line = line.split("]: ", 1)[1]
    line = _ANSI.sub("", line)
    line = _MC_CODE.sub("", line)
    line = _SYSTEM_PREFIX.sub("", line)
    line = _BRACKET_PREFIX.sub("", line)
    m = _VICTIM.match(line)
    if not m or not _DEATH_REST.match(m.group("rest")):
        return
    name = m.group("name").lower()
    counts[name] = counts.get(name, 0) + 1


def _scan_latest(path: Path, st, entry: dict | None) -> dict:
    """Read only the new part of latest.log; a partial last line waits for its newline."""
    inode = (st.st_dev, st.st_ino)
    if entry and entry.get("inode") == inode and st.st_size >= entry.get("offset", 0):
        if (st.st_size, st.st_mtime_ns) == (entry.get("size"), entry.get("mtime")):
            return entry
        counts = dict(entry["counts"])
        start = entry["offset"]
    else:  # first read or the log was rotated/truncated
        counts = {}
        start = 0
    with path.open("rb") as fh:
        fh.seek(start)
        offset = start
        for raw in fh:
            if not raw.endswith(b"\n"):
                break
            offset = fh.tell()
            _count_line(raw, counts)
    return {"inode": inode, "offset": offset, "counts": counts,
            "size": st.st_size, "mtime": st.st_mtime_ns}


def _scan_rotated(path: Path, st, entry: dict | None) -> dict:
    signature = (st.st_dev, st.st_ino, st.st_mtime_ns, st.st_size)
    if entry and entry.get("signature") == signature:
        return entry
    counts: dict[str, int] = {}
    opener = gzip.open if path.name.lower().endswith(".gz") else open
    with opener(path, "rb") as fh:
        for raw in fh:
            _count_line(raw, counts)
    return {"signature": signature, "counts": counts}


def _log_deaths(logs: Path) -> dict[str, int]:
    try:
        files = [p for p in logs.iterdir() if p.name == "latest.log" or _ROTATED_LOG.match(p.name)]
    except OSError:
        return {}
    totals: dict[str, int] = {}
    with _cache_lock:
        alive = {str(p) for p in files}
        for old in [key for key in _cache if key not in alive]:
            _cache.pop(old, None)
        for path in files:
            key = str(path)
            try:
                st = path.stat()
            except OSError:
                continue
            entry = _scan_latest(path, st, _cache.get(key)) if path.name == "latest.log" \
                else _scan_rotated(path, st, _cache.get(key))
            _cache[key] = entry
            for name, count in entry["counts"].items():
                totals[name] = totals.get(name, 0) + count
    return totals


def death_counts(inst: "ServerInstance", known: dict[str, dict], online: Iterable[str]) -> dict[str, int]:
    """Deaths per lowercase player key, counted from the logs available on disk."""
    totals = _log_deaths(Path(inst.dir) / "logs")
    counts = {key: totals.get(str(entry.get("name") or key).lower(), 0) for key, entry in known.items()}
    for name in online:
        key = name.lower()
        counts.setdefault(key, totals.get(key, 0))
    return counts


# ====================================================================== world statistics (1.13+)

def supported(inst: "ServerInstance") -> bool:
    """Player statistics files exist since Minecraft 1.13.

    Unknown labels (snapshots, custom builds) are treated as modern – the files either exist
    and can be read, or the player simply has no statistics to show.
    """
    match = _VERSION.match(str(getattr(inst.cfg, "mc_version", "") or ""))
    if not match:
        return True
    major, minor = int(match.group(1)), int(match.group(2))
    return major > 1 or (major == 1 and minor >= 13)


def _norm_uuid(value: Any) -> str | None:
    try:
        return str(uuidlib.UUID(str(value).strip()))
    except (ValueError, AttributeError, TypeError):
        return None


def norm_uuid(value: Any) -> str | None:
    """Canonical dashed UUID (lowercase), or None for anything that is not a UUID."""
    return _norm_uuid(value)


def _read_json(path: Path, retries: int = 3, delay: float = 0.05) -> Any:
    """Read JSON that the server may be writing at the same moment (Windows locks it briefly)."""
    for attempt in range(retries):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, PermissionError, OSError, UnicodeDecodeError):
            if attempt == retries - 1:
                raise
            time.sleep(delay)


def level_name(inst: "ServerInstance") -> str:
    level = (inst.props().get("level-name") or "world").strip()
    return level or "world"


def stats_dirs(inst: "ServerInstance") -> list[Path]:
    """The main world's stats folder plus other worlds (Multiverse and similar plugins).

    Since Minecraft 26.1 player files moved under ``<world>/players/`` (stats -> players/stats);
    older versions keep them in ``<world>/stats``. The new layout wins when both exist so a
    migrated world is not counted twice.
    """
    root = Path(inst.dir)
    primary = root / level_name(inst)
    result: list[Path] = []
    seen: set[str] = set()

    def add(path: Path) -> None:
        try:
            key = str(path.resolve())
        except OSError:
            key = str(path)
        if key not in seen:
            seen.add(key)
            result.append(path)

    for candidate in (primary / "players" / "stats", primary / "stats"):
        if candidate.is_dir():
            add(candidate)
            break
    try:
        for path in root.glob("*/players/stats"):
            if path.is_dir():
                add(path)
        for path in root.glob("*/stats"):
            if path.is_dir() and path.parent != primary:
                add(path)
    except OSError:
        pass
    return result


def _stats_files(inst: "ServerInstance", wanted: set[str] | None = None):
    """Yield (uuid, path, mtime) for every valid statistics file on disk."""
    for directory in stats_dirs(inst):
        try:
            files = list(directory.glob("*.json"))
        except OSError:
            continue
        for path in files:
            player_uuid = _norm_uuid(path.stem)
            if not player_uuid or not _UUID_FILE.match(path.stem):
                continue
            if wanted is not None and player_uuid not in wanted:
                continue
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            yield player_uuid, path, mtime


def _add_names(names: dict[str, str], uid: Any, name: Any) -> None:
    canonical = _norm_uuid(uid)
    if canonical and isinstance(name, str) and name.strip():
        names[canonical] = name.strip()


def _list_names(path: Path) -> list[dict]:
    try:
        data = _read_json(path)
        return data if isinstance(data, list) else []
    except (FileNotFoundError, json.JSONDecodeError, PermissionError, OSError, UnicodeDecodeError):
        return []


def _log_names(inst: "ServerInstance", wanted: set[str]) -> dict[str, str]:
    """Names of players who fell out of usercache, recovered from the server logs."""
    logs = Path(inst.dir) / "logs"
    if not wanted or not logs.is_dir():
        return {}
    files: list[Path] = []
    latest = logs / "latest.log"
    if latest.is_file():
        files.append(latest)
    try:
        files += sorted((p for p in logs.iterdir() if _ROTATED_LOG.match(p.name)), reverse=True)
    except OSError:
        pass
    found: dict[str, str] = {}
    for path in files:
        opener = gzip.open if path.name.lower().endswith(".gz") else open
        try:
            with opener(path, "rb") as fh:
                for raw in fh:
                    match = _LOG_UUID.search(raw.decode("utf-8", "replace"))
                    if not match:
                        continue
                    canonical = _norm_uuid(match.group(2))
                    if canonical and canonical in wanted:
                        found[canonical] = match.group(1)
        except OSError:
            continue
        if len(found) == len(wanted):
            break
    return found


def uuid_names(inst: "ServerInstance", wanted: set[str] | None = None) -> dict[str, str]:
    """UUID -> nickname from the panel's own store and every cache Minecraft/Forge writes."""
    names: dict[str, str] = {}
    known = inst.players_store.data.get("known", {})
    for key, entry in known.items():
        _add_names(names, entry.get("uuid"), entry.get("name") or key)

    root = Path(inst.dir)
    for path in (root / "usercache.json",):
        for entry in _list_names(path):
            _add_names(names, entry.get("uuid"), entry.get("name"))
    for path in (root / "whitelist.json", root / "ops.json"):
        for entry in _list_names(path):
            _add_names(names, entry.get("uuid"), entry.get("name"))
    try:  # Forge/NeoForge: a flat {"uuid": "name"} dictionary
        cache = _read_json(root / "usernamecache.json")
        if isinstance(cache, dict):
            for uid, name in cache.items():
                _add_names(names, uid, name)
    except (FileNotFoundError, json.JSONDecodeError, PermissionError, OSError, UnicodeDecodeError):
        pass

    if wanted:
        missing = set(wanted) - set(names)
        if missing:
            names.update(_log_names(inst, missing))
    return names


def _merge_stats(target: dict[str, dict[str, int]], stats: dict) -> None:
    for category, values in stats.items():
        if not isinstance(values, dict):
            continue
        bucket = target.setdefault(str(category), {})
        for key, value in values.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                bucket[str(key)] = bucket.get(str(key), 0) + value


def _raw_stats(inst: "ServerInstance", wanted: set[str]) -> dict[str, dict]:
    """Merge per-player statistics from every world (Multiverse keeps one stats folder each)."""
    players: dict[str, dict] = {}
    for player_uuid, path, mtime in _stats_files(inst, wanted):
        try:
            raw = _read_json(path)
        except (json.JSONDecodeError, PermissionError, OSError):
            continue
        stats = raw.get("stats") if isinstance(raw, dict) else None
        if not isinstance(stats, dict):
            continue  # legacy flat JSON (< 1.13) – this feature needs the modern format
        entry = players.setdefault(player_uuid, {"stats": {}, "data_version": raw.get("DataVersion")})
        entry["mtime"] = max(entry.get("mtime", 0), mtime)
        _merge_stats(entry["stats"], stats)
    return players


def _top(category: dict[str, int], limit: int = 8) -> list[dict]:
    items = sorted(category.items(), key=lambda kv: kv[1], reverse=True)
    return [{"id": key, "count": value} for key, value in items[:limit] if value]


def _normalize(stats: dict[str, dict[str, int]]) -> dict:
    custom = stats.get("minecraft:custom", {})

    def value(key: str) -> int:
        raw = custom.get(key, 0)
        return int(raw) if isinstance(raw, (int, float)) and not isinstance(raw, bool) else 0

    play_ticks = custom.get("minecraft:play_time", custom.get("minecraft:play_one_minute", 0))
    play_ticks = int(play_ticks) if isinstance(play_ticks, (int, float)) and not isinstance(play_ticks, bool) else 0
    distance_cm = sum(v for key, v in custom.items()
                      if key.endswith("_one_cm") and isinstance(v, (int, float)) and not isinstance(v, bool))

    mined = stats.get("minecraft:mined", {})
    crafted = stats.get("minecraft:crafted", {})
    return {
        "play_time_seconds": round(play_ticks / 20, 1),
        "play_time_hours": round(play_ticks / 72000, 2),
        "deaths": value("minecraft:deaths"),
        "mob_kills": value("minecraft:mob_kills"),
        "player_kills": value("minecraft:player_kills"),
        "jumps": value("minecraft:jump"),
        "distance_km": round(distance_cm / 100_000, 2),
        "blocks_mined": int(sum(mined.values())),
        "items_crafted": int(sum(crafted.values())),
        "top": {
            "mined": _top(mined),
            "crafted": _top(crafted),
            "killed": _top(stats.get("minecraft:killed", {})),
            "killed_by": _top(stats.get("minecraft:killed_by", {})),
            "used": _top(stats.get("minecraft:used", {})),
            "broken": _top(stats.get("minecraft:broken", {})),
            "picked_up": _top(stats.get("minecraft:picked_up", {})),
            "dropped": _top(stats.get("minecraft:dropped", {})),
        },
    }


def player_stats(inst: "ServerInstance", player_uuid: str) -> dict | None:
    """Normalized statistics of one player, or None when the world has no file for them."""
    canonical = _norm_uuid(player_uuid)
    if not canonical:
        return None
    records = _raw_stats(inst, {canonical})
    record = records.get(canonical)
    if not record:
        return None
    known = inst.players_store.data.get("known", {})
    name = uuid_names(inst, {canonical}).get(canonical)
    if not name:
        name = next((entry.get("name") for entry in known.values()
                     if _norm_uuid(entry.get("uuid")) == canonical), None)
    last_seen = next((entry.get("last_seen") for entry in known.values()
                      if _norm_uuid(entry.get("uuid")) == canonical and entry.get("last_seen")), None)
    return {
        "uuid": canonical,
        "name": name,
        "last_seen": last_seen or record.get("mtime"),
        "data_version": record.get("data_version"),
        **_normalize(record["stats"]),
    }


def sync_known(inst: "ServerInstance") -> None:
    """Import players found in world stats files into the panel's persistent player store.

    Joins/leaves already keep nicknames and UUIDs, but a fresh panel (or one installed after
    the server had been running) would otherwise only know players from the current caches.
    The stats file's modification time is a good approximation of the last time played.
    """
    if not supported(inst):
        return
    files = list(_stats_files(inst))
    if not files:
        return
    data = inst.players_store.data
    known = data.setdefault("known", {})
    changed = False

    # Merge entries that already refer to the same UUID (a premium player changed their nick).
    # last_seen must never shrink: a naive dict.update would overwrite the newer value with the
    # older one when the entry that survives the merge is not the one carrying the newest visit.
    by_uuid: dict[str, tuple[str, dict]] = {}
    for key, entry in list(known.items()):
        canonical = _norm_uuid(entry.get("uuid"))
        if not canonical:
            continue
        if canonical in by_uuid:
            other_key, other = by_uuid[canonical]
            merged = {k: v for k, v in other.items() if v not in (None, "")}
            for k, v in entry.items():
                if v in (None, ""):
                    continue
                if k == "last_seen":
                    merged[k] = max(merged.get(k, 0), v)
                elif not merged.get(k):
                    merged[k] = v
            if entry.get("last_seen", 0) >= other.get("last_seen", 0):
                known.pop(other_key, None)
                known[key] = merged
                by_uuid[canonical] = (key, merged)
            else:
                known.pop(key, None)
                known[other_key] = merged
                by_uuid[canonical] = (other_key, merged)
            changed = True
        else:
            by_uuid[canonical] = (key, entry)

    wanted = {uid for uid, _, _ in files}
    names = uuid_names(inst, wanted)
    for player_uuid, _, mtime in files:
        name = names.get(player_uuid)
        if not name or not re.match(r"^[A-Za-z0-9_]{1,16}$", name):
            continue
        if player_uuid in by_uuid:
            entry = by_uuid[player_uuid][1]
            if not entry.get("name"):
                entry["name"] = name
                changed = True
            if not entry.get("uuid"):
                entry["uuid"] = player_uuid
                changed = True
            if not entry.get("last_seen"):
                entry["last_seen"] = mtime
                changed = True
            continue
        key = name.lower()
        entry = known.get(key)
        if entry is None:
            entry = {"name": name, "uuid": player_uuid, "last_seen": mtime}
            known[key] = entry
            changed = True
        else:
            if not entry.get("uuid"):
                entry["uuid"] = player_uuid
                changed = True
            if not entry.get("last_seen"):
                entry["last_seen"] = mtime
                changed = True
        by_uuid[player_uuid] = (key, entry)

    if changed:
        inst.players_store.save()

