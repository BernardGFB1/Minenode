"""Detect and disable client-only mods on a server.

Modpacks are built for players, and many of them ship client-only mods (shaders, minimaps, menu tweaks…)
without marking them in their index. Loading those on a dedicated server crashes it. We combine three signals:

1. Modrinth project metadata (``server_side == "unsupported"``), looked up by the file's SHA-1,
2. the mod's own metadata (Fabric/Quilt ``environment: client``, Forge/NeoForge ``clientSideOnly``),
3. a crash analyzer: a mod that crashes the server with a client-only error is disabled automatically.

Mods that another kept mod *requires* are never disabled, so dependency chains stay intact. Disabled mods are
only renamed to ``.jar.disabled``, so they can be re-enabled from the Mods tab.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import re
import zipfile
from pathlib import Path
from typing import Callable

import httpx

from . import net

Log = Callable[[str], None]
MODRINTH = "https://api.modrinth.com/v2"
# Mods that only make sense on a player's PC and break dedicated servers even though they don't say so.
KNOWN_SERVER_BREAKERS = {
    "missingmodschecker",  # pops up a "download missing mods" window (Swing) – crashes headless servers
}
# Platform ids that are never "mods" we could remove.
IGNORED_DEPS = {"minecraft", "java", "fabricloader", "fabric-loader", "quilt_loader", "forge", "neoforge",
                "fabric", "quilted_fabric_api", "mixinextras"}


def _sha1(p: Path) -> str:
    h = hashlib.sha1()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _loads(raw: bytes) -> dict:
    try:
        return json.loads(raw.decode("utf-8", "replace"), strict=False)
    except json.JSONDecodeError:
        return {}


def _deps(value) -> set[str]:
    if isinstance(value, dict):
        return set(value)
    if isinstance(value, list):  # quilt: ["id", {"id": "x", "optional": false}]
        out = set()
        for v in value:
            if isinstance(v, str):
                out.add(v)
            elif isinstance(v, dict) and v.get("id") and not v.get("optional"):
                out.add(v["id"].split(":")[-1])
        return out
    return set()


def _toml_mods(text: str) -> tuple[set[str], set[str], bool]:
    """Minimal mods.toml reader: (mod ids, required dependency ids, client-only flag)."""
    ids = set(re.findall(r'^\s*modId\s*=\s*"([^"]+)"', text.split("[[dependencies", 1)[0], re.M))
    deps = set()
    for block in re.split(r"\[\[dependencies\.[^\]]+\]\]", text)[1:]:
        dep = re.search(r'modId\s*=\s*"([^"]+)"', block)
        mandatory = re.search(r"mandatory\s*=\s*true", block) or re.search(r'type\s*=\s*"required"', block)
        if dep and mandatory:
            deps.add(dep.group(1))
    client = bool(re.search(r"^\s*clientSideOnly\s*=\s*true", text, re.M))
    return ids, deps, client


def read_meta(jar: Path) -> dict:
    """{ids, depends, client} from fabric.mod.json / quilt.mod.json / (neoforge.)mods.toml, incl. nested jars."""
    ids: set[str] = set()
    depends: set[str] = set()
    client = False

    def scan(zf: zipfile.ZipFile, top: bool) -> None:
        nonlocal client
        names = set(zf.namelist())
        if "fabric.mod.json" in names:
            m = _loads(zf.read("fabric.mod.json"))
            if m.get("id"):
                ids.add(m["id"])
            ids.update(m.get("provides", []) or [])
            if top:
                depends.update(_deps(m.get("depends")))
                client = client or m.get("environment") == "client"
        if "quilt.mod.json" in names:
            m = _loads(zf.read("quilt.mod.json"))
            ql = m.get("quilt_loader", {})
            if ql.get("id"):
                ids.add(ql["id"])
            if top:
                depends.update(_deps(ql.get("depends")))
                client = client or (m.get("minecraft") or {}).get("environment") == "client"
        for toml in ("META-INF/neoforge.mods.toml", "META-INF/mods.toml"):
            if toml in names:
                tid, tdeps, tclient = _toml_mods(zf.read(toml).decode("utf-8", "replace"))
                ids.update(tid)
                if top:
                    depends.update(tdeps)
                    client = client or tclient
        if top:  # jar-in-jar libraries provide ids too
            for n in names:
                if n.startswith("META-INF/jars/") and n.endswith(".jar"):
                    try:
                        scan(zipfile.ZipFile(io.BytesIO(zf.read(n))), False)
                    except (zipfile.BadZipFile, OSError):
                        pass

    try:
        with zipfile.ZipFile(jar) as zf:
            scan(zf, True)
    except (zipfile.BadZipFile, OSError):
        pass
    return {"ids": ids, "depends": depends - IGNORED_DEPS, "client": client}


async def _modrinth_client_only(hashes: list[str]) -> set[str]:
    """SHA-1s whose Modrinth project is marked as not supported on servers."""
    if not hashes:
        return set()
    try:
        versions = await net.post_json(f"{MODRINTH}/version_files", {"hashes": hashes, "algorithm": "sha1"})
        pids = sorted({v["project_id"] for v in versions.values()})
        projects: dict[str, dict] = {}
        for i in range(0, len(pids), 100):
            for p in await net.get_json(f"{MODRINTH}/projects", params={"ids": json.dumps(pids[i:i + 100])}):
                projects[p["id"]] = p
    except (httpx.HTTPError, ValueError):
        return set()
    return {h for h, v in versions.items() if projects.get(v["project_id"], {}).get("server_side") == "unsupported"}


def _mark(server_dir: Path, file: str, reason: str) -> None:
    meta_path = server_dir / ".minenode" / "content.json"
    try:
        meta = json.loads(meta_path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        meta = {"files": {}, "hashes": {}}
    meta.setdefault("files", {}).setdefault(file, {})["disabled_reason"] = reason
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps(meta, indent=1, ensure_ascii=False))


def disable(server_dir: Path, jar: Path, reason: str) -> None:
    target = jar.with_name(jar.name + ".disabled")
    if target.exists():
        target.unlink()
    jar.rename(target)
    _mark(server_dir, jar.name, reason)


async def prune(server_dir: Path, log: Log) -> list[str]:
    """Disable client-only mods in <server>/mods that no kept mod depends on. Returns disabled file names."""
    mods = server_dir / "mods"
    if not mods.is_dir():
        return []
    jars = sorted(p for p in mods.iterdir() if p.is_file() and p.suffix == ".jar")
    if not jars:
        return []
    log(f"Sprawdzanie {len(jars)} modów pod kątem modów tylko dla klienta…")
    metas = await asyncio.to_thread(lambda: {j: read_meta(j) for j in jars})
    hashes = await asyncio.to_thread(lambda: {j: _sha1(j) for j in jars})
    flagged = await _modrinth_client_only(list(hashes.values()))
    candidates = {j for j in jars if metas[j]["client"] or hashes[j] in flagged
                  or metas[j]["ids"] & KNOWN_SERVER_BREAKERS}
    if not candidates:
        log("Nie znaleziono modów tylko dla klienta.")
        return []
    # keep candidates that a kept mod requires (fixpoint – requirements can be chained)
    kept = set(jars) - candidates
    changed = True
    while changed:
        changed = False
        required = set().union(*(metas[j]["depends"] for j in kept)) if kept else set()
        for j in list(candidates):
            if metas[j]["ids"] & required:
                candidates.discard(j)
                kept.add(j)
                changed = True
    disabled = []
    for j in sorted(candidates):
        await asyncio.to_thread(disable, server_dir, j, "client")
        disabled.append(j.name)
    log(f"Wyłączono {len(disabled)} modów tylko dla klienta (można je włączyć w zakładce Mody): "
        + ", ".join(d.removesuffix(".jar") for d in disabled[:25]) + ("…" if len(disabled) > 25 else ""))
    return disabled


# ---------------------------------------------------------------------------- crash analysis

CLIENT_CRASH = re.compile(
    r"HeadlessException|No X11 DISPLAY|invalid dist DEDICATED_SERVER|in environment type SERVER|"
    r"net[./]minecraft[./]client[./]|com[./]mojang[./]blaze3d|org[./]lwjgl[./](opengl|glfw)|"
    r"Environment type SERVER", re.I)
JAR_FRAME = re.compile(r"~\[([^\]\s]+?\.jar):[^\]]*\]")
SKIP_JARS = re.compile(r"^(fabric-loader|fabric-server-launch|server|minecraft|forge|neoforge|fmlloader|"
                       r"securejarhandler|bootstraplauncher|modlauncher|quilt-loader|knot)", re.I)


def find_crashing_client_mod(lines: list[str], server_dir: Path) -> Path | None:
    """Return the mods/ jar that crashed the server with a client-only error, if it can be identified."""
    text = "\n".join(lines)
    if not CLIENT_CRASH.search(text):
        return None
    mods = server_dir / "mods"
    start = max(0, len(lines) - 400)
    for i in range(start, len(lines)):
        if not CLIENT_CRASH.search(lines[i]):
            continue
        for line in lines[i:i + 80]:  # stack trace below the error
            for jar in JAR_FRAME.findall(line):
                name = jar.rsplit("/", 1)[-1]
                if SKIP_JARS.match(name):
                    continue
                candidate = mods / name
                if candidate.exists():
                    return candidate
        mod = re.search(r"(?:from mod|apply for mod) ([a-z0-9_\-]+)", "\n".join(lines[i:i + 40]))
        if mod:
            for jar in mods.glob("*.jar"):
                if mod.group(1) in read_meta(jar)["ids"]:
                    return jar
    return None
