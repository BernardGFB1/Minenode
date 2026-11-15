"""Datapacks of the server's main world (``<level-name>/datapacks/``).

Minecraft loads every ``.zip`` and folder it finds there on start. A disabled pack is moved out of the world
to ``.minenode/datapacks-disabled/`` – that works the same for zips and folders and survives world resets.
Panel metadata (source, version, icon) lives in ``.minenode/datapacks.json`` keyed by file name.
"""
from __future__ import annotations

import json
import re
import shutil
import time
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING

from . import net
from .config import TMP_DIR
from .content import resolve_modrinth
from .modpacks import MODRINTH, ModpackError
from .properties import read_properties
from .store import atomic_write_text
from .worldgen import WorldgenError, inspect

if TYPE_CHECKING:
    from .manager import ServerInstance

MAX_SIZE = 512 * 1024 * 1024
_UNSAFE = re.compile(r"[^A-Za-z0-9._ +()\[\]-]+")


class DatapackError(ModpackError):
    pass


def level_name(inst: "ServerInstance") -> str:
    return read_properties(inst.dir / "server.properties").get("level-name") or "world"


def active_dir(inst: "ServerInstance") -> Path:
    return inst.dir / level_name(inst) / "datapacks"


def disabled_dir(inst: "ServerInstance") -> Path:
    return inst.dir / ".minenode" / "datapacks-disabled"


def _meta_path(inst: "ServerInstance") -> Path:
    return inst.dir / ".minenode" / "datapacks.json"


def _load_meta(inst: "ServerInstance") -> dict:
    try:
        data = json.loads(_meta_path(inst).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_meta(inst: "ServerInstance", meta: dict) -> None:
    _meta_path(inst).parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(_meta_path(inst), json.dumps(meta, indent=2, ensure_ascii=False))


def _is_pack(p: Path) -> bool:
    if p.name.startswith("."):
        return False
    if p.is_dir():
        return (p / "pack.mcmeta").is_file()
    return p.is_file() and p.suffix.lower() == ".zip"


def _read_mcmeta(p: Path) -> dict:
    try:
        if p.is_dir():
            raw = (p / "pack.mcmeta").read_text(encoding="utf-8-sig")
        else:
            with zipfile.ZipFile(p) as zf:
                raw = zf.read("pack.mcmeta").decode("utf-8-sig")
        pack = json.loads(raw).get("pack", {})
    except (OSError, KeyError, ValueError, zipfile.BadZipFile, AttributeError):
        return {}
    desc = pack.get("description", "") if isinstance(pack, dict) else ""
    if not isinstance(desc, str):
        desc = _flatten_text(desc)
    return {"description": desc[:200], "pack_format": pack.get("pack_format") if isinstance(pack, dict) else None}


def _flatten_text(component) -> str:
    """A JSON text component ("text", "extra", lists) as plain text."""
    if isinstance(component, str):
        return component
    if isinstance(component, list):
        return "".join(_flatten_text(c) for c in component)
    if isinstance(component, dict):
        return str(component.get("text", "")) + "".join(_flatten_text(c) for c in component.get("extra", []))
    return ""


def _has_icon(p: Path) -> bool:
    try:
        if p.is_dir():
            return (p / "pack.png").is_file()
        with zipfile.ZipFile(p) as zf:
            return "pack.png" in zf.namelist()
    except (OSError, zipfile.BadZipFile):
        return False


def _size(p: Path) -> int:
    if p.is_file():
        return p.stat().st_size
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())


def list_packs(inst: "ServerInstance") -> list[dict]:
    meta = _load_meta(inst)
    out = []
    for enabled, folder in ((True, active_dir(inst)), (False, disabled_dir(inst))):
        if not folder.is_dir():
            continue
        for p in sorted(folder.iterdir(), key=lambda x: x.name.lower()):
            if not _is_pack(p):
                continue
            m = meta.get(p.name, {})
            out.append({
                "file": p.name, "name": m.get("title") or (p.stem if p.is_file() else p.name),
                "enabled": enabled, "folder": p.is_dir(), "size": _size(p), "has_icon": _has_icon(p),
                "source": m.get("source", "upload"), "version": m.get("version"), "icon_url": m.get("icon_url"),
                "project_id": m.get("project_id"), **_read_mcmeta(p)})
    return out


def _find(inst: "ServerInstance", file: str) -> tuple[Path, bool]:
    if not file or "/" in file or "\\" in file or file in (".", "..") or file.startswith("."):
        raise DatapackError("Nieprawidłowa nazwa datapacka")
    for enabled, folder in ((True, active_dir(inst)), (False, disabled_dir(inst))):
        p = folder / file
        if p.exists() and _is_pack(p):
            return p, enabled
    raise FileNotFoundError(file)


def _safe_filename(name: str) -> str:
    stem = _UNSAFE.sub("_", Path(name).stem).strip(" ._") or f"datapack-{int(time.time())}"
    return f"{stem[:100]}.zip"


def add_file(inst: "ServerInstance", tmp: Path, filename: str, info: dict | None = None) -> dict:
    """Validate an uploaded/downloaded zip and put it into the world's datapacks folder."""
    try:
        if tmp.stat().st_size > MAX_SIZE:
            raise DatapackError("Plik jest za duży (limit 512 MB)")
        try:
            found = inspect(tmp)
        except WorldgenError as exc:
            raise DatapackError(str(exc)) from exc
        if found["kind"] != "datapack":
            what = "plugin" if found["kind"] == "plugin" else "mod"
            raise DatapackError(f"To {what}, a nie datapack – dodaj go w zakładce Pluginy/Mody")
        name = _safe_filename(filename)
        for folder in (active_dir(inst), disabled_dir(inst)):  # replacing a pack keeps a single copy
            old = folder / name
            if old.is_file():
                old.unlink()
        dest = active_dir(inst) / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(tmp), dest)
    finally:
        tmp.unlink(missing_ok=True)
    meta = _load_meta(inst)
    meta[name] = {"source": "upload", **(info or {})}
    _save_meta(inst, meta)
    inst.panel_log(f"Dodano datapack {name}")
    return next(p for p in list_packs(inst) if p["file"] == name) | {"fixed": bool(found.get("fixed"))}


async def install_modrinth(inst: "ServerInstance", project_id: str) -> dict:
    v, _ = await resolve_modrinth(project_id, inst.cfg.mc_version, ["datapack"], "datapack")
    project = await net.get_json(f"{MODRINTH}/project/{v['project_id']}")
    zips = [f for f in v.get("files", []) if f.get("filename", "").lower().endswith(".zip")]
    if not zips:
        raise DatapackError("Ta wersja nie zawiera datapacka (.zip)")
    f = next((x for x in zips if x.get("primary")), zips[0])
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    tmp = TMP_DIR / f"dp-{int(time.time() * 1000)}.zip"
    inst.panel_log(f"Pobieranie datapacka {project['title']} {v['version_number']}…")
    await net.download(f["url"], tmp, sha1=f.get("hashes", {}).get("sha1"))
    return add_file(inst, tmp, f["filename"], {
        "source": "modrinth", "title": project["title"], "project_id": v["project_id"],
        "version": v["version_number"], "icon_url": project.get("icon_url")})


def toggle(inst: "ServerInstance", file: str, enabled: bool) -> None:
    path, now = _find(inst, file)
    if now == enabled:
        return
    target = (active_dir(inst) if enabled else disabled_dir(inst)) / file
    if target.exists():
        raise DatapackError("Datapack o tej nazwie już istnieje w miejscu docelowym")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(path), target)
    inst.panel_log(f"Datapack {file} {'włączony' if enabled else 'wyłączony'}")


def remove(inst: "ServerInstance", file: str) -> None:
    path, _ = _find(inst, file)
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()
    meta = _load_meta(inst)
    if meta.pop(file, None) is not None:
        _save_meta(inst, meta)
    inst.panel_log(f"Usunięto datapack {file}")


def icon(inst: "ServerInstance", file: str) -> bytes | None:
    path, _ = _find(inst, file)
    try:
        if path.is_dir():
            return (path / "pack.png").read_bytes() if (path / "pack.png").is_file() else None
        with zipfile.ZipFile(path) as zf:
            return zf.read("pack.png")
    except (KeyError, OSError, zipfile.BadZipFile):
        return None
