"""Mods & plugins: search (Modrinth / CurseForge), one-click install with dependencies,
installed-content inventory and automatic compatibility verification."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

from . import net
from .modpacks import CURSEFORGE, MODRINTH, ModpackError, _cf_cdn_urls, _cf_get, _cf_headers
from .store import atomic_write_text

if TYPE_CHECKING:
    from .manager import ServerInstance

# Which Modrinth/CurseForge loader tags a given server engine can run.
LOADER_COMPAT: dict[str, list[str]] = {
    "paper": ["paper", "spigot", "bukkit"],
    "purpur": ["purpur", "paper", "spigot", "bukkit"],
    "fabric": ["fabric"],
    "quilt": ["quilt", "fabric"],
    "forge": ["forge"],
    "neoforge": ["neoforge"],
    "vanilla": [],
    "custom": [],
}
LOADER_TAGS = {"fabric", "forge", "neoforge", "quilt", "paper", "spigot", "bukkit", "purpur", "folia", "velocity",
               "bungeecord", "waterfall", "sponge", "liteloader", "modloader", "rift", "minecraft", "datapack", "iris",
               "optifine", "canvas", "vanilla"}
CF_LOADER_TYPE = {"forge": 1, "fabric": 4, "quilt": 5, "neoforge": 6}
CF_CLASS = {"mod": 6, "plugin": 5, "resourcepack": 12}


def kind_for(engine: str) -> str | None:
    if engine in ("paper", "purpur"):
        return "plugin"
    if engine in ("fabric", "quilt", "forge", "neoforge"):
        return "mod"
    return None


def content_dir(inst: "ServerInstance") -> Path:
    return inst.dir / ("plugins" if kind_for(inst.cfg.type) == "plugin" else "mods")


def _meta_path(inst: "ServerInstance") -> Path:
    return inst.dir / ".minenode" / "content.json"


def load_meta(inst: "ServerInstance") -> dict:
    try:
        return json.loads(_meta_path(inst).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {"files": {}, "hashes": {}}


def save_meta(inst: "ServerInstance", meta: dict) -> None:
    atomic_write_text(_meta_path(inst), json.dumps(meta, indent=1, ensure_ascii=False))


def mc_minor(v: str) -> str:
    """'1.21.4' -> '1.21' ; '26.1.2' -> '26.1'."""
    parts = v.split(".")
    return ".".join(parts[:2])


def _sha1(path: Path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


# ====================================================================== search

async def modrinth_search(project_type: str, query: str, offset: int, mc: str | None, loaders: list[str],
                          limit: int = 20) -> dict:
    facets: list[list[str]] = [[f"project_type:{project_type}"]]
    if loaders:
        facets.append([f"categories:{l}" for l in loaders])
    if mc:
        facets.append([f"versions:{mc}"])
    if project_type == "mod":
        facets.append(["server_side:required", "server_side:optional"])
    data = await net.get_json(f"{MODRINTH}/search", params={
        "query": query, "limit": limit, "offset": offset, "facets": json.dumps(facets),
        "index": "relevance" if query else "downloads"})
    return {"total": data.get("total_hits", 0), "items": [{
        "id": h["project_id"], "slug": h.get("slug"), "title": h["title"], "description": h.get("description", ""),
        "icon": h.get("icon_url"), "downloads": h.get("downloads", 0), "author": h.get("author"),
        "categories": [c for c in h.get("display_categories", h.get("categories", [])) if c not in LOADER_TAGS][:3],
        "server_side": h.get("server_side"), "client_side": h.get("client_side"),
        "updated": h.get("date_modified"), "source": "modrinth",
        "url": f"https://modrinth.com/{project_type}/{h.get('slug') or h['project_id']}",
    } for h in data.get("hits", [])]}


async def curseforge_search(api_key: str, project_type: str, query: str, offset: int, mc: str | None,
                            engine: str, limit: int = 20) -> dict:
    params: dict[str, Any] = {"gameId": 432, "classId": CF_CLASS[project_type], "searchFilter": query,
                              "sortField": 2, "sortOrder": "desc", "pageSize": limit, "index": offset}
    if mc and project_type != "plugin":
        params["gameVersion"] = mc
    if project_type == "mod" and engine in CF_LOADER_TYPE:
        params["modLoaderType"] = CF_LOADER_TYPE[engine]
    data = await _cf_get("/mods/search", api_key, params)
    return {"total": data.get("pagination", {}).get("totalCount", 0), "items": [{
        "id": str(m["id"]), "slug": m.get("slug"), "title": m["name"], "description": m.get("summary", ""),
        "icon": (m.get("logo") or {}).get("thumbnailUrl"), "downloads": m.get("downloadCount", 0),
        "author": ", ".join(a["name"] for a in m.get("authors", [])[:2]),
        "categories": [c["name"] for c in m.get("categories", [])][:3],
        "updated": m.get("dateModified"), "source": "curseforge", "url": (m.get("links") or {}).get("websiteUrl"),
    } for m in data.get("data", [])]}


# ====================================================================== version resolution

async def modrinth_versions(project_id: str, mc: str | None, loaders: list[str]) -> list[dict]:
    params = {}
    if loaders:
        params["loaders"] = json.dumps(loaders)
    if mc:
        params["game_versions"] = json.dumps([mc])
    return await net.get_json(f"{MODRINTH}/project/{project_id}/version", params=params)


def _pick(versions: list[dict]) -> dict | None:
    if not versions:
        return None
    rel = [v for v in versions if v.get("version_type") == "release"]
    return (rel or versions)[0]


async def resolve_modrinth(project_id: str, mc: str, loaders: list[str], kind: str) -> tuple[dict, str]:
    """Return (version, compat) – the newest compatible version for this server."""
    v = _pick(await modrinth_versions(project_id, mc, loaders))
    if v:
        return v, "ok"
    if kind in ("plugin", "resourcepack"):
        # plugins/resource packs often still work on newer patch releases – try same minor line, then newest
        all_versions = await modrinth_versions(project_id, None, loaders)
        same = [x for x in all_versions if any(mc_minor(g) == mc_minor(mc) for g in x.get("game_versions", []))]
        if same:
            return _pick(same), "warn"
        if all_versions:
            return _pick(all_versions), "warn"
    raise ModpackError(f"Brak wersji zgodnej z {'/'.join(loaders) or 'Minecraft'} {mc}")


async def cf_files(api_key: str, mod_id: str, mc: str | None, engine: str, kind: str) -> list[dict]:
    params: dict[str, Any] = {"pageSize": 50}
    if mc and kind != "plugin":
        params["gameVersion"] = mc
    if kind == "mod" and engine in CF_LOADER_TYPE:
        params["modLoaderType"] = CF_LOADER_TYPE[engine]
    return (await _cf_get(f"/mods/{mod_id}/files", api_key, params)).get("data", [])


def cf_compat(file: dict, mc: str, engine: str, kind: str) -> str:
    gv = file.get("gameVersions", [])
    lower = {g.lower() for g in gv}
    if kind == "mod" and engine in CF_LOADER_TYPE:
        allowed = {l for l in LOADER_COMPAT[engine]}
        if lower & {"forge", "fabric", "quilt", "neoforge"} and not lower & allowed:
            return "bad"
    if mc in gv:
        return "ok"
    if any(mc_minor(g) == mc_minor(mc) for g in gv if re.match(r"^\d", g)):
        return "warn"
    return "bad" if kind == "mod" else "warn"


async def resolve_cf(api_key: str, mod_id: str, mc: str, engine: str, kind: str) -> tuple[dict, str]:
    files = await cf_files(api_key, mod_id, mc, engine, kind)
    if not files and kind != "mod":
        files = await cf_files(api_key, mod_id, None, engine, kind)
    files = [f for f in files if not f.get("isServerPack")]
    if not files:
        raise ModpackError(f"Brak pliku zgodnego z {engine} {mc}")
    files.sort(key=lambda f: (f.get("releaseType") != 1, f.get("fileDate", "")), reverse=False)
    ranked = sorted(files, key=lambda f: ({"ok": 0, "warn": 1, "bad": 2}[cf_compat(f, mc, engine, kind)],
                                          f.get("releaseType", 3)), )
    # newest among the best compatibility class
    best = cf_compat(ranked[0], mc, engine, kind)
    pool = [f for f in files if cf_compat(f, mc, engine, kind) == best]
    pool.sort(key=lambda f: f.get("fileDate", ""), reverse=True)
    rel = [f for f in pool if f.get("releaseType") == 1]
    return (rel or pool)[0], best


# ====================================================================== install

class Installer:
    def __init__(self, inst: "ServerInstance", api_key: str = ""):
        self.inst = inst
        self.api_key = api_key
        self.engine = inst.cfg.type
        self.mc = inst.cfg.mc_version
        self.kind = kind_for(self.engine)
        self.loaders = LOADER_COMPAT[self.engine]
        self.installed: list[dict] = []
        self.meta = load_meta(inst)
        self._seen: set[str] = set()

    def _installed_projects(self) -> set[str]:
        return {f.get("project_id") for f in self.meta["files"].values() if f.get("project_id")}

    def _remove_project_files(self, d: Path, project_id: str, keep: str) -> None:
        for fname, info in list(self.meta["files"].items()):
            if info.get("project_id") == project_id and fname != keep:
                for p in (d / fname, d / (fname + ".disabled")):
                    p.unlink(missing_ok=True)
                self.meta["files"].pop(fname, None)

    async def modrinth(self, project_id: str, version_id: str | None = None, depth: int = 0) -> None:
        if not self.kind:
            raise ModpackError("Serwer Vanilla nie obsługuje modów ani pluginów")
        if project_id in self._seen or depth > 6:
            return
        self._seen.add(project_id)
        if version_id:
            v = await net.get_json(f"{MODRINTH}/version/{version_id}")
            ok_loader = bool(set(v.get("loaders", [])) & set(self.loaders))
            compat = "ok" if ok_loader and self.mc in v.get("game_versions", []) else "warn" if ok_loader else "bad"
            if compat == "bad":
                raise ModpackError(f"Wersja {v['version_number']} nie obsługuje silnika {self.engine}")
        else:
            v, compat = await resolve_modrinth(project_id, self.mc, self.loaders, self.kind)
        project = await net.get_json(f"{MODRINTH}/project/{v['project_id']}")
        f = next((x for x in v["files"] if x.get("primary")), v["files"][0])
        d = content_dir(self.inst)
        dest = d / Path(f["filename"]).name
        self.inst.panel_log(f"Pobieranie {project['title']} {v['version_number']}…")
        await net.download(f["url"], dest, sha1=f.get("hashes", {}).get("sha1"))
        self._remove_project_files(d, v["project_id"], dest.name)
        self.meta["files"][dest.name] = {
            "source": "modrinth", "project_id": v["project_id"], "version_id": v["id"],
            "title": project["title"], "icon": project.get("icon_url"), "version_number": v["version_number"],
            "slug": project.get("slug"), "installed_at": time.time(), "sha1": f.get("hashes", {}).get("sha1"),
            "dependency": depth > 0,
        }
        self.installed.append({"title": project["title"], "version": v["version_number"], "compat": compat,
                               "dependency": depth > 0})
        save_meta(self.inst, self.meta)
        for dep in v.get("dependencies", []):
            if dep.get("dependency_type") != "required" or not (dep.get("project_id") or dep.get("version_id")):
                continue
            pid = dep.get("project_id")
            if not pid and dep.get("version_id"):
                pid = (await net.get_json(f"{MODRINTH}/version/{dep['version_id']}"))["project_id"]
            if pid in self._installed_projects() and pid not in self._seen:
                continue
            try:
                await self.modrinth(pid, None, depth + 1)
            except ModpackError as exc:
                self.inst.panel_log(f"  ⚠ Zależność {pid}: {exc}")

    async def curseforge(self, mod_id: str, file_id: str | None = None, depth: int = 0) -> None:
        if not self.kind:
            raise ModpackError("Serwer Vanilla nie obsługuje modów ani pluginów")
        if mod_id in self._seen or depth > 6:
            return
        self._seen.add(mod_id)
        mod = (await _cf_get(f"/mods/{mod_id}", self.api_key))["data"]
        if file_id:
            f = (await _cf_get(f"/mods/{mod_id}/files/{file_id}", self.api_key))["data"]
            compat = cf_compat(f, self.mc, self.engine, self.kind)
        else:
            f, compat = await resolve_cf(self.api_key, mod_id, self.mc, self.engine, self.kind)
        if compat == "bad" and depth == 0 and not file_id:
            raise ModpackError(f"{mod['name']}: brak pliku zgodnego z {self.engine} {self.mc}")
        d = content_dir(self.inst)
        dest = d / Path(f["fileName"]).name
        urls = ([f["downloadUrl"]] if f.get("downloadUrl") else []) + _cf_cdn_urls(int(f["id"]), f["fileName"])
        sha1 = next((h["value"] for h in f.get("hashes", []) if h.get("algo") == 1), None)
        self.inst.panel_log(f"Pobieranie {mod['name']} ({f['fileName']})…")
        last: Exception | None = None
        for url in urls:
            try:
                await net.download(url, dest, sha1=sha1)
                last = None
                break
            except Exception as exc:  # noqa: BLE001
                last = exc
        if last:
            raise ModpackError(f"Nie udało się pobrać {f['fileName']} – autor mógł zablokować pobieranie przez API")
        self._remove_project_files(d, str(mod["id"]), dest.name)
        self.meta["files"][dest.name] = {
            "source": "curseforge", "project_id": str(mod["id"]), "version_id": str(f["id"]),
            "title": mod["name"], "icon": (mod.get("logo") or {}).get("thumbnailUrl"),
            "version_number": f.get("displayName") or f["fileName"], "installed_at": time.time(),
            "game_versions": f.get("gameVersions", []), "sha1": sha1, "dependency": depth > 0,
        }
        self.installed.append({"title": mod["name"], "version": f.get("displayName"), "compat": compat,
                               "dependency": depth > 0})
        save_meta(self.inst, self.meta)
        for dep in f.get("dependencies", []):
            if dep.get("relationType") == 3:
                dep_id = str(dep["modId"])
                if dep_id in self._installed_projects():
                    continue
                try:
                    await self.curseforge(dep_id, None, depth + 1)
                except Exception as exc:  # noqa: BLE001
                    self.inst.panel_log(f"  ⚠ Zależność {dep_id}: {exc}")


# ====================================================================== inventory & verification

_verify_cache: dict[tuple, tuple[float, dict]] = {}


def _scan(inst: "ServerInstance") -> list[dict]:
    d = content_dir(inst)
    meta = load_meta(inst)
    hashes = meta.setdefault("hashes", {})
    out = []
    if d.exists():
        for p in sorted(d.iterdir(), key=lambda x: x.name.lower()):
            if not p.is_file() or not (p.name.endswith(".jar") or p.name.endswith(".jar.disabled")):
                continue
            st = p.stat()
            base = p.name[:-9] if p.name.endswith(".disabled") else p.name
            cached = hashes.get(p.name)
            if cached and cached["size"] == st.st_size and cached["mtime"] == st.st_mtime:
                sha1 = cached["sha1"]
            else:
                sha1 = _sha1(p)
                hashes[p.name] = {"size": st.st_size, "mtime": st.st_mtime, "sha1": sha1}
            info = meta["files"].get(base, {})
            out.append({"file": base, "enabled": not p.name.endswith(".disabled"), "size": st.st_size,
                        "sha1": sha1, "title": info.get("title") or re.sub(r"[-_+]?\d.*$", "", base[:-4]) or base,
                        "icon": info.get("icon"), "version_number": info.get("version_number"),
                        "project_id": info.get("project_id"), "source": info.get("source"),
                        "version_id": info.get("version_id"), "dependency": info.get("dependency", False),
                        "disabled_reason": info.get("disabled_reason") if p.name.endswith(".disabled") else None,
                        "game_versions": info.get("game_versions")})
    # drop hash cache for removed files
    names = {p.name for p in d.iterdir()} if d.exists() else set()
    for k in list(hashes):
        if k not in names:
            hashes.pop(k)
    save_meta(inst, meta)
    return out


async def inventory(inst: "ServerInstance", check: bool = True) -> dict:
    kind = kind_for(inst.cfg.type)
    if not kind:
        return {"kind": None, "items": [], "checked": False}
    items = await asyncio.to_thread(_scan, inst)
    if check and items:
        await _verify(inst, items)
    return {"kind": kind, "items": items, "checked": check, "loaders": LOADER_COMPAT[inst.cfg.type],
            "mc_version": inst.cfg.mc_version, "dir": content_dir(inst).name}


async def _verify(inst: "ServerInstance", items: list[dict]) -> None:
    """Identify files on Modrinth by SHA-1 and check game version + loader. Also finds updates."""
    mc, engine, kind = inst.cfg.mc_version, inst.cfg.type, kind_for(inst.cfg.type)
    loaders = LOADER_COMPAT[engine]
    hashes = [i["sha1"] for i in items]
    key = (inst.cfg.id, mc, engine, tuple(sorted(hashes)))
    hit = _verify_cache.get(key)
    if hit and time.time() - hit[0] < 600:
        res = hit[1]
    else:
        res = {"versions": {}, "updates": {}, "projects": {}}
        try:
            res["versions"] = await net.post_json(f"{MODRINTH}/version_files",
                                                  {"hashes": hashes, "algorithm": "sha1"})
            res["updates"] = await net.post_json(f"{MODRINTH}/version_files/update", {
                "hashes": hashes, "algorithm": "sha1", "loaders": loaders, "game_versions": [mc]})
            pids = sorted({v["project_id"] for v in res["versions"].values()})
            if pids:
                projects = await net.get_json(f"{MODRINTH}/projects", params={"ids": json.dumps(pids)})
                res["projects"] = {p["id"]: p for p in projects}
            _verify_cache[key] = (time.time(), res)
        except (httpx.HTTPError, ValueError):
            pass
    for it in items:
        v = res["versions"].get(it["sha1"])
        if v:
            p = res["projects"].get(v["project_id"], {})
            it["project_id"] = it["project_id"] or v["project_id"]
            it["source"] = it["source"] or "modrinth"
            it["title"] = p.get("title") or it["title"]
            it["icon"] = it["icon"] or p.get("icon_url")
            it["version_number"] = it["version_number"] or v["version_number"]
            gv, lv = v.get("game_versions", []), v.get("loaders", [])
            if not set(lv) & set(loaders) and not _jar_supports(inst, it["file"], it["enabled"], engine):
                it["compat"], it["reason"] = "bad", f"wersja dla {', '.join(lv)}, serwer: {engine}"
            elif mc in gv:
                it["compat"], it["reason"] = "ok", f"zgodny z {mc}"
            elif any(mc_minor(g) == mc_minor(mc) for g in gv):
                it["compat"], it["reason"] = "warn", f"oznaczony dla {', '.join(gv[-3:])}"
            else:
                it["compat"] = "warn" if kind == "plugin" else "bad"
                it["reason"] = f"oznaczony dla {', '.join(gv[-3:])}, serwer: {mc}"
            up = res["updates"].get(it["sha1"])
            if up and up["id"] != v["id"]:
                it["update"] = {"version_id": up["id"], "version_number": up["version_number"]}
        elif it.get("source") == "curseforge" and it.get("game_versions"):
            c = cf_compat({"gameVersions": it["game_versions"]}, mc, engine, kind)
            it["compat"] = c
            it["reason"] = f"pliki CurseForge: {', '.join([g for g in it['game_versions'] if re.match(r'^\\d', g)][-3:])}"
        else:
            it["compat"], it["reason"] = "unknown", "nie znaleziono w Modrinth – nie można zweryfikować"


def _jar_supports(inst: "ServerInstance", file: str, enabled: bool, engine: str) -> bool:
    """Many mods publish one file for several loaders – check the jar's own metadata as well."""
    p = content_dir(inst) / (file if enabled else file + ".disabled")
    try:
        with zipfile.ZipFile(p) as zf:
            names = set(zf.namelist())
    except (zipfile.BadZipFile, OSError):
        return False
    if engine in ("fabric", "quilt"):
        return "fabric.mod.json" in names or "quilt.mod.json" in names
    if engine == "forge":
        return "META-INF/mods.toml" in names
    if engine == "neoforge":
        return "META-INF/neoforge.mods.toml" in names or "META-INF/mods.toml" in names
    if engine in ("paper", "purpur"):
        return "plugin.yml" in names or "paper-plugin.yml" in names
    return False


async def warn_incompatible(inst: "ServerInstance") -> None:
    """Background check at server start – writes warnings into the console."""
    try:
        if not kind_for(inst.cfg.type):
            return
        data = await inventory(inst, check=True)
        bad = [i for i in data["items"] if i["enabled"] and i.get("compat") == "bad"]
        if bad:
            inst.panel_log(f"⚠ Weryfikacja: {len(bad)} {'pluginów' if data['kind'] == 'plugin' else 'modów'} "
                           f"może nie działać na {inst.cfg.type} {inst.cfg.mc_version}:")
            for i in bad[:15]:
                inst.panel_log(f"   • {i['title']} ({i['file']}) – {i.get('reason', '')}")
        elif data["items"]:
            inst.panel_log(f"✔ Weryfikacja: {len(data['items'])} plików w {data['dir']}/ – brak wykrytych niezgodności.")
    except Exception:  # noqa: BLE001 – never break a start because of the check
        pass


def toggle(inst: "ServerInstance", file: str, enabled: bool) -> None:
    d = content_dir(inst)
    src = d / (file if enabled is False else file + ".disabled")
    dst = d / (file + ".disabled" if enabled is False else file)
    if not src.resolve().is_relative_to(d.resolve()) or not dst.resolve().is_relative_to(d.resolve()):
        raise ValueError("bad path")
    if src.exists():
        src.rename(dst)


def remove(inst: "ServerInstance", file: str) -> None:
    d = content_dir(inst)
    for p in (d / file, d / (file + ".disabled")):
        if not p.resolve().is_relative_to(d.resolve()):
            raise ValueError("bad path")
        p.unlink(missing_ok=True)
    meta = load_meta(inst)
    meta["files"].pop(file, None)
    save_meta(inst, meta)
