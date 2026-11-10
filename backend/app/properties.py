"""Java .properties reader/writer that preserves key order (server.properties)."""
from __future__ import annotations

import time
from pathlib import Path

from .store import atomic_write_text

MANAGED_KEYS = {"server-port"}  # controlled by the panel (internal port behind the proxy)


def _unescape(s: str) -> str:
    out, i = [], 0
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s):
            n = s[i + 1]
            if n == "u" and i + 5 < len(s):
                try:
                    out.append(chr(int(s[i + 2:i + 6], 16)))
                    i += 6
                    continue
                except ValueError:
                    pass
            out.append({"t": "\t", "n": "\n", "r": "\r", "f": "\f"}.get(n, n))
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _escape(s: str, is_key: bool = False) -> str:
    out = []
    for i, c in enumerate(s):
        if c == "\\":
            out.append("\\\\")
        elif c in "=:#!":
            out.append("\\" + c)
        elif c == "\n":
            out.append("\\n")
        elif c == "\t":
            out.append("\\t")
        elif c == " " and (is_key or i == 0):
            out.append("\\ ")
        elif ord(c) > 0x7E:
            out.append(f"\\u{ord(c):04x}" if ord(c) <= 0xFFFF else c)
        else:
            out.append(c)
    return "".join(out)


def read_properties(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    props: dict[str, str] = {}
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].lstrip()
        i += 1
        if not line or line[0] in "#!":
            continue
        while line.endswith("\\") and not line.endswith("\\\\") and i < len(lines):
            line = line[:-1] + lines[i].lstrip()
            i += 1
        # find first unescaped separator
        sep = None
        j = 0
        while j < len(line):
            if line[j] == "\\":
                j += 2
                continue
            if line[j] in "=:":
                sep = j
                break
            j += 1
        if sep is None:
            key, value = line, ""
        else:
            key, value = line[:sep], line[sep + 1:]
        props[_unescape(key.strip())] = _unescape(value.lstrip())
    return props


def write_properties(path: Path, props: dict[str, str]) -> None:
    lines = ["#Minecraft server properties", f"#Managed by MineNode – {time.strftime('%a %b %d %H:%M:%S %Y')}"]
    for k, v in props.items():
        lines.append(f"{_escape(k, True)}={_escape(str(v))}")
    atomic_write_text(path, "\n".join(lines) + "\n")


def update_properties(path: Path, changes: dict[str, str]) -> dict[str, str]:
    props = read_properties(path)
    for k, v in changes.items():
        if v is None:
            props.pop(k, None)
        else:
            props[k] = "true" if v is True else "false" if v is False else str(v)
    write_properties(path, props)
    return props
