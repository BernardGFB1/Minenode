"""Modpack import from Modrinth (.mrpack) and CurseForge (manifest.json / server packs)."""
from __future__ import annotations

import asyncio
import json
import re
import shutil
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable

import httpx

from . import net
from .backups import safe_extract
from .config import TMP_DIR

Log = Callable[[str], None]
Progress = Callable[[float, str], None]

MODRINTH = "https://api.modrinth.com/v2"
CURSEFORGE = "https://api.curseforge.com/v1"
CF_GAME_MINECRAFT = 432
CF_CLASS_MODPACK = 4471
CF_CLASS_MOD = 6

LOADER_MAP = {
    "fabric-loader": "fabric", "quilt-loader": "quilt", "forge": "forge", "neoforge": "neoforge",
}


class ModpackError(RuntimeError):
    pass


@dataclass
class ModpackPlan:
    name: str
    engine: str
    mc_version: str
    loader_version: str | None
    apply: Callable[[Path, Log, Progress], Awaitable[None]]
    meta: dict = field(default_factory=dict)


def _safe_target(root: Path, rel: str) -> Path:
    target = (root / rel).resolve()
    if not target.is_relative_to(root.resolve()) or target == root.resolve():
        raise ModpackError(f"Niebezpieczna ścieżka w paczce: {rel}")
    return target


async def _download_many(jobs: list[dict], log: Log, progress: Progress, label: str, headers=None) -> list[dict]:
    """jobs: {url(s), dest, sha1?, sha512?, name}. Returns failed jobs."""
    sem = asyncio.Semaphore(8)
    done = 0
    failed: list[dict] = []

    async def one(job: dict) -> None:
        nonlocal done
        async with sem:
            urls = job.get("urls") or [job["url"]]
            for i, url in enumerate(urls):
                try:
                    await net.download(url, job["dest"], sha1=job.get("sha1"), sha512=job.get("sha512"),
                                       headers=headers)
                    break
                except Exception as exc:
                    if i == len(urls) - 1:
                        failed.append({**job, "error": str(exc)})
                        log(f"  ✗ {job['name']}: {exc}")
            done += 1
            progress(done / max(1, len(jobs)), f"{label} {done}/{len(jobs)}")

    await asyncio.gather(*(one(j) for j in jobs))
    return failed


def _extract_overrides(zf: zipfile.ZipFile, server_dir: Path, folders: list[str], log: Log) -> None:
    names = zf.namelist()
    for folder in folders:
        prefix = folder.rstrip("/") + "/"
        if any(n.startswith(prefix) for n in names):
            log(f"Kopiowanie plików z „{folder}”…")
            safe_extract(zf, server_dir, strip_prefix=prefix)


# ===================================================================== Modrinth

async def modrinth_search(query: str, offset: int = 0, limit: int = 20, mc: str | None = None,
                          loader: str | None = None) -> dict:
    facets = [["project_type:modpack"]]
    if mc:
        facets.append([f"versions:{mc}"])
    if loader:
        facets.append([f"categories:{loader}"])
    params = {"query": query, "limit": limit, "offset": offset, "facets": json.dumps(facets),
              "index": "relevance" if query else "downloads"}
    data = await net.get_json(f"{MODRINTH}/search", params=params)
    return {
        "total": data.get("total_hits", 0),
        "items": [{
            "id": h["project_id"], "slug": h.get("slug"), "title": h["title"], "description": h.get("description", ""),
            "icon": h.get("icon_url"), "downloads": h.get("downloads", 0), "follows": h.get("follows", 0),
            "author": h.get("author"), "versions": h.get("versions", [])[-3:],
            "loaders": [c for c in h.get("categories", []) if c in ("fabric", "forge", "neoforge", "quilt")],
            "updated": h.get("date_modified"), "source": "modrinth",
            "url": f"https://modrinth.com/modpack/{h.get('slug') or h['project_id']}",
        } for h in data.get("hits", [])],
    }


async def modrinth_versions(project_id: str) -> list[dict]:
    data = await net.get_json(f"{MODRINTH}/project/{project_id}/version")
    out = []
    for v in data:
        primary = next((f for f in v["files"] if f.get("primary")), v["files"][0] if v["files"] else None)
        if not primary:
            continue
        out.append({
            "id": v["id"], "name": v.get("name") or v["version_number"], "version": v["version_number"],
            "game_versions": v.get("game_versions", []), "loaders": v.get("loaders", []),
            "type": v.get("version_type", "release"), "date": v.get("date_published"),
            "size": primary.get("size"), "file": primary["filename"],
        })
    return out


async def modrinth_plan_from_version(version_id: str, log: Log) -> ModpackPlan:
    v = await net.get_json(f"{MODRINTH}/version/{version_id}")
    primary = next((f for f in v["files"] if f.get("primary")), v["files"][0])
    project = await net.get_json(f"{MODRINTH}/project/{v['project_id']}")
    dest = TMP_DIR / f"mrpack-{version_id}-{int(time.time())}.mrpack"
    log(f"Pobieranie {primary['filename']} ({(primary.get('size') or 0) / 1048576:.1f} MB)…")
    await net.download(primary["url"], dest, sha1=primary.get("hashes", {}).get("sha1"))
    plan = mrpack_plan(dest, log)
    plan.name = project.get("title") or plan.name
    plan.meta.update({"icon_url": project.get("icon_url"), "version_name": v.get("version_number")})
    return plan


def mrpack_plan(archive: Path, log: Log) -> ModpackPlan:
    try:
        zf = zipfile.ZipFile(archive)
        index = json.loads(zf.read("modrinth.index.json"))
    except (zipfile.BadZipFile, KeyError, json.JSONDecodeError) as exc:
        raise ModpackError("To nie jest poprawny plik .mrpack") from exc
    deps = index.get("dependencies", {})
    mc = deps.get("minecraft")
    if not mc:
        raise ModpackError("Paczka nie określa wersji Minecrafta")
    engine, loader = "vanilla", None
    for key, eng in LOADER_MAP.items():
        if key in deps:
            engine, loader = eng, deps[key]
            break

    async def apply(server_dir: Path, log: Log, progress: Progress) -> None:
        try:
            jobs, skipped = [], 0
            for f in index.get("files", []):
                env = f.get("env") or {}
                if env.get("server") == "unsupported" or f["path"].startswith(("resourcepacks/", "shaderpacks/")):
                    skipped += 1
                    continue
                dest = _safe_target(server_dir, f["path"])
                jobs.append({"urls": f.get("downloads", []), "dest": dest, "name": f["path"],
                             "sha1": f.get("hashes", {}).get("sha1"), "sha512": f.get("hashes", {}).get("sha512")})
            log(f"Pobieranie {len(jobs)} plików modpacka (pominięto {skipped} tylko-klienckich)…")
            failed = await _download_many(jobs, log, progress, "Mody")
            _extract_overrides(zf, server_dir, ["overrides", "server-overrides"], log)
            if failed:
                log(f"UWAGA: {len(failed)} plików nie udało się pobrać – sprawdź konsolę.")
            log("Modpack zainstalowany.")
        finally:
            zf.close()
            archive.unlink(missing_ok=True)

    return ModpackPlan(name=index.get("name", archive.stem), engine=engine, mc_version=mc,
                       loader_version=loader, apply=apply, meta={"version_name": index.get("versionId", "")})


# ===================================================================== CurseForge

def _cf_headers(api_key: str) -> dict:
    if not api_key:
        raise ModpackError("Brak klucza API CurseForge – ustaw go w Ustawieniach panelu.")
    return {"x-api-key": api_key, "Accept": "application/json"}


async def _cf_get(path: str, api_key: str, params: dict | None = None) -> dict:
    try:
        return await net.get_json(f"{CURSEFORGE}{path}", headers=_cf_headers(api_key), params=params)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 403:
            raise ModpackError("CurseForge odrzucił klucz API (403).") from exc
        raise


async def curseforge_search(api_key: str, query: str, offset: int = 0, limit: int = 20,
                            mc: str | None = None) -> dict:
    params = {"gameId": CF_GAME_MINECRAFT, "classId": CF_CLASS_MODPACK, "searchFilter": query,
              "sortField": 2, "sortOrder": "desc", "pageSize": limit, "index": offset}
    if mc:
        params["gameVersion"] = mc
    data = await _cf_get("/mods/search", api_key, params)
    items = []
    for m in data.get("data", []):
        latest = m.get("latestFilesIndexes") or []
        items.append({
            "id": str(m["id"]), "slug": m.get("slug"), "title": m["name"], "description": m.get("summary", ""),
            "icon": (m.get("logo") or {}).get("thumbnailUrl"), "downloads": m.get("downloadCount", 0),
            "author": ", ".join(a["name"] for a in m.get("authors", [])[:2]),
            "versions": sorted({x.get("gameVersion") for x in latest if x.get("gameVersion")})[-3:],
            "loaders": [], "updated": m.get("dateModified"), "source": "curseforge",
            "url": (m.get("links") or {}).get("websiteUrl"),
        })
    return {"total": data.get("pagination", {}).get("totalCount", 0), "items": items}


async def curseforge_files(api_key: str, mod_id: str) -> list[dict]:
    data = await _cf_get(f"/mods/{mod_id}/files", api_key, {"pageSize": 50})
    out = []
    for f in data.get("data", []):
        if f.get("isServerPack"):
            continue
        out.append({
            "id": str(f["id"]), "name": f.get("displayName") or f["fileName"], "version": f["fileName"],
            "game_versions": [g for g in f.get("gameVersions", []) if re.match(r"^\d", g)],
            "loaders": [g.lower() for g in f.get("gameVersions", []) if g.lower() in ("forge", "fabric", "neoforge", "quilt")],
            "type": {1: "release", 2: "beta", 3: "alpha"}.get(f.get("releaseType"), "release"),
            "date": f.get("fileDate"), "size": f.get("fileLength"),
            "server_pack": bool(f.get("serverPackFileId")),
        })
    out.sort(key=lambda x: x["date"] or "", reverse=True)
    return out


def _cf_cdn_urls(file_id: int, file_name: str) -> list[str]:
    a, b = file_id // 1000, file_id % 1000
    return [f"https://edge.forgecdn.net/files/{a}/{b}/{file_name}",
            f"https://mediafilez.forgecdn.net/files/{a}/{b}/{file_name}"]


def _cf_parse_loader(manifest: dict) -> tuple[str, str | None]:
    loaders = manifest.get("minecraft", {}).get("modLoaders", [])
    primary = next((l for l in loaders if l.get("primary")), loaders[0] if loaders else None)
    if not primary:
        return "vanilla", None
    lid = primary["id"]
    name, _, ver = lid.partition("-")
    engine = {"forge": "forge", "neoforge": "neoforge", "fabric": "fabric", "quilt": "quilt"}.get(name.lower())
    if not engine:
        raise ModpackError(f"Nieobsługiwany loader: {lid}")
    if engine == "forge" and ver.startswith(manifest["minecraft"]["version"] + "-"):
        ver = ver[len(manifest["minecraft"]["version"]) + 1:]
    return engine, ver or None


async def curseforge_plan_from_file(api_key: str, mod_id: str, file_id: str, log: Log) -> ModpackPlan:
    mod = (await _cf_get(f"/mods/{mod_id}", api_key))["data"]
    f = (await _cf_get(f"/mods/{mod_id}/files/{file_id}", api_key))["data"]
    dest = TMP_DIR / f"cf-{file_id}-{int(time.time())}.zip"
    urls = ([f["downloadUrl"]] if f.get("downloadUrl") else []) + _cf_cdn_urls(int(f["id"]), f["fileName"])
    log(f"Pobieranie {f['fileName']}…")
    failed = await _download_many([{"urls": urls, "dest": dest, "name": f["fileName"]}], log, lambda *_: None, "Paczka")
    if failed:
        raise ModpackError(f"Nie udało się pobrać paczki: {failed[0]['error']}")
    server_pack = None
    if f.get("serverPackFileId"):
        sp = (await _cf_get(f"/mods/{mod_id}/files/{f['serverPackFileId']}", api_key))["data"]
        server_pack = {"urls": ([sp["downloadUrl"]] if sp.get("downloadUrl") else []) +
                       _cf_cdn_urls(int(sp["id"]), sp["fileName"]), "name": sp["fileName"]}
    plan = curseforge_zip_plan(dest, api_key, log, server_pack)
    plan.name = mod.get("name") or plan.name
    plan.meta.update({"icon_url": (mod.get("logo") or {}).get("thumbnailUrl"), "version_name": f.get("displayName")})
    return plan


def curseforge_zip_plan(archive: Path, api_key: str, log: Log, server_pack: dict | None = None) -> ModpackPlan:
    try:
        zf = zipfile.ZipFile(archive)
        manifest = json.loads(zf.read("manifest.json"))
    except (zipfile.BadZipFile, KeyError, json.JSONDecodeError) as exc:
        raise ModpackError("To nie jest poprawna paczka CurseForge (brak manifest.json)") from exc
    mc = manifest.get("minecraft", {}).get("version")
    if not mc:
        raise ModpackError("Paczka nie określa wersji Minecrafta")
    engine, loader = _cf_parse_loader(manifest)

    async def apply(server_dir: Path, log: Log, progress: Progress) -> None:
        try:
            if server_pack:
                await _apply_cf_server_pack(server_pack, server_dir, log, progress)
            else:
                await _apply_cf_manifest(manifest, api_key, server_dir, log, progress)
            _extract_overrides(zf, server_dir, [manifest.get("overrides") or "overrides"], log)
            log("Modpack zainstalowany.")
        finally:
            zf.close()
            archive.unlink(missing_ok=True)

    return ModpackPlan(name=manifest.get("name", archive.stem), engine=engine, mc_version=mc,
                       loader_version=loader, apply=apply, meta={"version_name": manifest.get("version", "")})


async def _apply_cf_server_pack(sp: dict, server_dir: Path, log: Log, progress: Progress) -> None:
    dest = TMP_DIR / f"cf-serverpack-{int(time.time())}.zip"
    log(f"Pobieranie oficjalnej paczki serwerowej {sp['name']}…")
    failed = await _download_many([{**sp, "dest": dest}], log, progress, "Server pack")
    if failed:
        raise ModpackError("Nie udało się pobrać paczki serwerowej")
    try:
        with zipfile.ZipFile(dest) as zf:
            names = [n for n in zf.namelist() if n.strip("/")]
            tops = {n.split("/", 1)[0] for n in names}
            prefix = ""
            if len(tops) == 1 and all("/" in n for n in names):
                prefix = next(iter(tops)) + "/"
            safe_extract(zf, server_dir, strip_prefix=prefix)
        log("Rozpakowano paczkę serwerową.")
    finally:
        dest.unlink(missing_ok=True)


async def _apply_cf_manifest(manifest: dict, api_key: str, server_dir: Path, log: Log, progress: Progress) -> None:
    entries = [e for e in manifest.get("files", []) if e.get("required", True)]
    if not entries:
        return
    headers = _cf_headers(api_key)
    file_ids = [int(e["fileID"]) for e in entries]
    mod_ids = list({int(e["projectID"]) for e in entries})
    files = (await net.post_json(f"{CURSEFORGE}/mods/files", {"fileIds": file_ids}, headers=headers)).get("data", [])
    mods = (await net.post_json(f"{CURSEFORGE}/mods", {"modIds": mod_ids, "filterPcOnly": False},
                                headers=headers)).get("data", [])
    mod_class = {m["id"]: m.get("classId") for m in mods}
    mod_names = {m["id"]: m.get("name") for m in mods}
    jobs, skipped, manual = [], 0, []
    for f in files:
        cls = mod_class.get(f["modId"], CF_CLASS_MOD)
        gv = f.get("gameVersions", [])
        if cls != CF_CLASS_MOD or ("Client" in gv and "Server" not in gv):
            skipped += 1
            continue
        urls = ([f["downloadUrl"]] if f.get("downloadUrl") else []) + _cf_cdn_urls(int(f["id"]), f["fileName"])
        if not f.get("downloadUrl"):
            manual.append(mod_names.get(f["modId"], f["fileName"]))
        sha1 = next((h["value"] for h in f.get("hashes", []) if h.get("algo") == 1), None)
        jobs.append({"urls": urls, "dest": _safe_target(server_dir, f"mods/{f['fileName']}"),
                     "name": f["fileName"], "sha1": sha1})
    log(f"Pobieranie {len(jobs)} modów z CurseForge (pominięto {skipped} zasobów klienta)…")
    failed = await _download_many(jobs, log, progress, "Mody")
    if failed:
        log(f"UWAGA: nie pobrano {len(failed)} modów (autor mógł zablokować pobieranie przez API). "
            "Wgraj je ręcznie do folderu mods/: " + ", ".join(j["name"] for j in failed))


# ===================================================================== File upload

def plan_from_upload(archive: Path, api_key: str, log: Log) -> ModpackPlan:
    try:
        with zipfile.ZipFile(archive) as zf:
            names = set(zf.namelist())
    except zipfile.BadZipFile as exc:
        raise ModpackError("Plik nie jest archiwum ZIP/.mrpack") from exc
    if "modrinth.index.json" in names:
        return mrpack_plan(archive, log)
    if "manifest.json" in names:
        return curseforge_zip_plan(archive, api_key, log)
    raise ModpackError("Nie rozpoznano formatu paczki (oczekiwano .mrpack lub ZIP z CurseForge)")
