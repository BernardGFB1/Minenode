"""Per-player statistics derived from the server logs.

Vanilla writes the death broadcast to the log, so deaths are counted from those lines.
Both the current ``latest.log`` and rotated logs are read; the current file is consumed
incrementally so refreshing the public page does not re-read the whole session.
"""
from __future__ import annotations

import gzip
import re
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:
    from .manager import ServerInstance

# "2026-11-19-3.log" / "2026-11-19-3.log.gz" – Minecraft's rotated logs. debug.log and
# other files in logs/ are deliberately ignored (they can repeat the same lines).
_ROTATED_LOG = re.compile(r"^\d{4}-\d{2}-\d{2}-\d+\.log(?:\.gz)?$", re.I)
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_MC_CODE = re.compile(r"§[0-9A-FK-ORa-fk-or]")
_SYSTEM_PREFIX = re.compile(r"^(?:\[Not Secure\]\s*|System chat:\s*)+")
_BRACKET_PREFIX = re.compile(r"^(?:\[[^\]]*\]\s*)+")

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
