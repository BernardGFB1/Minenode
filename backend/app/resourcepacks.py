"""Resource pack hosting: packs are stored next to the server, served publicly by the panel over HTTP
and wired into server.properties (resource-pack URL + SHA-1) with one click."""
from __future__ import annotations

import hashlib
import json
import secrets
import shutil
import time
import uuid
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING

from . import net
from .config import PANEL_PORT, TMP_DIR
from .content import CF_CLASS, resolve_cf, resolve_modrinth
from .modpacks import MODRINTH, ModpackError, _cf_cdn_urls, _cf_get
from .properties import read_properties, update_properties
from .store import atomic_write_text

if TYPE_CHECKING:
    from .manager import Manager, ServerInstance

MAX_SIZE = 250 * 1024 * 1024  # client limit (1.18+)


def packs_dir(inst: "ServerInstance") -> Path:
    return inst.dir / ".minenode" / "resourcepacks"


def _index_path(inst: "ServerInstance") -> Path:
    return packs_dir(inst) / "index.json"


def load_index(inst: "ServerInstance") -> list[dict]:
    try:
        return json.loads(_index_path(inst).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_index(inst: "ServerInstance", items: list[dict]) -> None:
    atomic_write_text(_index_path(inst), json.dumps(items, indent=1, ensure_ascii=False))


def public_base(m: "Manager") -> str | None:
    s = m.settings
    if s.public_panel_url:
        return s.public_panel_url.rstrip("/")
    host = s.base_domain or s.public_address
    return f"http://{host}:{PANEL_PORT}" if host else None


def pack_url(m: "Manager", inst: "ServerInstance", pack: dict) -> str | None:
    base = public_base(m)
    return f"{base}/rp/{inst.cfg.id}/{pack['token']}/{pack['id']}.zip" if base else None


def _describe(desc) -> str:
    if isinstance(desc, str):
        return desc
    if isinstance(desc, list):
        return "".join(_describe(d) for d in desc)
    if isinstance(desc, dict):
        return str(desc.get("text", "")) + "".join(_describe(e) for e in desc.get("extra", []))
    return ""


def _inspect_and_fix(path: Path) -> dict:
    """Validate pack.mcmeta; if the pack is wrapped in a single folder, re-zip it with that folder stripped."""
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        if "pack.mcmeta" not in names:
            candidates = [n for n in names if n.endswith("/pack.mcmeta") and n.count("/") == 1]
            if not candidates:
                raise ModpackError("To nie jest paczka zasobów – brak pliku pack.mcmeta")
            prefix = candidates[0][: -len("pack.mcmeta")]
            fixed = path.with_name(path.name + ".fixed")
            with zipfile.ZipFile(fixed, "w", zipfile.ZIP_DEFLATED) as out:
                for info in zf.infolist():
                    if info.filename.startswith(prefix) and info.filename != prefix:
                        out.writestr(info.filename[len(prefix):], zf.read(info))
            fixed.replace(path)
            return _inspect_and_fix(path) | {"fixed": True}
        try:
            meta = json.loads(zf.read("pack.mcmeta").decode("utf-8-sig"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ModpackError("Uszkodzony pack.mcmeta") from exc
        pack = meta.get("pack", {})
        return {"pack_format": pack.get("pack_format"), "description": _describe(pack.get("description", ""))[:200],
                "has_icon": "pack.png" in names, "fixed": False}


def _sha1(path: Path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def add_file(inst: "ServerInstance", src: Path, name: str, source: str = "upload", extra: dict | None = None) -> dict:
    if src.stat().st_size > MAX_SIZE:
        src.unlink(missing_ok=True)
        raise ModpackError("Paczka jest większa niż 250 MB – klient Minecrafta jej nie pobierze")
    if not zipfile.is_zipfile(src):
        src.unlink(missing_ok=True)
        raise ModpackError("Paczka zasobów musi być archiwum ZIP")
    try:
        info = _inspect_and_fix(src)
    except Exception:
        src.unlink(missing_ok=True)
        raise
    pid = uuid.uuid4().hex[:12]
    d = packs_dir(inst)
    d.mkdir(parents=True, exist_ok=True)
    dest = d / f"{pid}.zip"
    shutil.move(str(src), dest)
    pack = {"id": pid, "name": name, "source": source, "size": dest.stat().st_size, "sha1": _sha1(dest),
            "token": secrets.token_urlsafe(12), "added_at": time.time(), **info, **(extra or {})}
    items = load_index(inst)
    items.append(pack)
    save_index(inst, items)
    return pack


def get(inst: "ServerInstance", pid: str) -> tuple[dict, Path]:
    for p in load_index(inst):
        if p["id"] == pid:
            return p, packs_dir(inst) / f"{pid}.zip"
    raise FileNotFoundError(pid)


def active_id(inst: "ServerInstance", m: "Manager") -> str | None:
    url = read_properties(inst.dir / "server.properties").get("resource-pack", "")
    for p in load_index(inst):
        if p["token"] in url and p["id"] in url:
            return p["id"]
    return None


def activate(inst: "ServerInstance", m: "Manager", pid: str, require: bool, prompt: str) -> dict:
    pack, _ = get(inst, pid)
    url = pack_url(m, inst, pack)
    if not url:
        raise ModpackError("Ustaw „Publiczny adres panelu” w Ustawieniach – gracze muszą móc pobrać paczkę")
    update_properties(inst.dir / "server.properties", {
        "resource-pack": url, "resource-pack-sha1": pack["sha1"], "require-resource-pack": require,
        "resource-pack-prompt": json.dumps({"text": prompt}, ensure_ascii=False) if prompt else "",
        "resource-pack-id": str(uuid.uuid5(uuid.NAMESPACE_URL, pack["sha1"])),
    })
    return pack


def deactivate(inst: "ServerInstance") -> None:
    update_properties(inst.dir / "server.properties", {
        "resource-pack": "", "resource-pack-sha1": "", "require-resource-pack": False, "resource-pack-prompt": "",
        "resource-pack-id": "",
    })


def delete(inst: "ServerInstance", m: "Manager", pid: str) -> None:
    if active_id(inst, m) == pid:
        deactivate(inst)
    items = [p for p in load_index(inst) if p["id"] != pid]
    save_index(inst, items)
    (packs_dir(inst) / f"{pid}.zip").unlink(missing_ok=True)


def icon(inst: "ServerInstance", pid: str) -> bytes | None:
    _, path = get(inst, pid)
    with zipfile.ZipFile(path) as zf:
        try:
            return zf.read("pack.png")
        except KeyError:
            return None


async def install_modrinth(inst: "ServerInstance", project_id: str, version_id: str | None) -> dict:
    mc = inst.cfg.mc_version
    if version_id:
        v = await net.get_json(f"{MODRINTH}/version/{version_id}")
    else:
        v, _ = await resolve_modrinth(project_id, mc, ["minecraft"], "resourcepack")
    project = await net.get_json(f"{MODRINTH}/project/{v['project_id']}")
    f = next((x for x in v["files"] if x.get("primary")), v["files"][0])
    tmp = TMP_DIR / f"rp-{int(time.time() * 1000)}.zip"
    inst.panel_log(f"Pobieranie paczki zasobów {project['title']} {v['version_number']}…")
    await net.download(f["url"], tmp, sha1=f.get("hashes", {}).get("sha1"))
    return add_file(inst, tmp, project["title"], "modrinth", {
        "project_id": v["project_id"], "version": v["version_number"], "icon_url": project.get("icon_url"),
        "game_versions": v.get("game_versions", [])[-4:]})


async def install_curseforge(inst: "ServerInstance", api_key: str, mod_id: str) -> dict:
    mod = (await _cf_get(f"/mods/{mod_id}", api_key))["data"]
    if mod.get("classId") != CF_CLASS["resourcepack"]:
        raise ModpackError("Ten projekt CurseForge nie jest paczką zasobów")
    f, _ = await resolve_cf(api_key, mod_id, inst.cfg.mc_version, inst.cfg.type, "resourcepack")
    tmp = TMP_DIR / f"rp-{int(time.time() * 1000)}.zip"
    urls = ([f["downloadUrl"]] if f.get("downloadUrl") else []) + _cf_cdn_urls(int(f["id"]), f["fileName"])
    inst.panel_log(f"Pobieranie paczki zasobów {mod['name']}…")
    for i, url in enumerate(urls):
        try:
            await net.download(url, tmp)
            break
        except Exception:  # noqa: BLE001
            if i == len(urls) - 1:
                raise ModpackError("Nie udało się pobrać paczki z CurseForge")
    return add_file(inst, tmp, mod["name"], "curseforge", {
        "project_id": str(mod["id"]), "version": f.get("displayName"),
        "icon_url": (mod.get("logo") or {}).get("thumbnailUrl")})
