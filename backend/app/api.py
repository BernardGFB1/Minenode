"""REST + WebSocket API."""
from __future__ import annotations

import asyncio
import contextlib
import os
import platform
import re
import shutil
import time
import unicodedata
import uuid
import zipfile
from pathlib import Path
from typing import Any, Literal, Optional

import psutil
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile, WebSocket
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field, ValidationError
from starlette.websockets import WebSocketDisconnect

from . import storage as storage_mod
from . import auth, content, files, java as javamod, modpacks, players as pl, providers, resourcepacks as rpacks, voice
from .backups import safe_extract
from .config import BEDROCK_PORT, DATA_DIR, PROXY_PORT, SECURE_COOKIES, TMP_DIR, VERSION, CURSEFORGE_API_KEY_ENV
from .manager import Manager, ServerError, ServerInstance
from .models import BackupSchedule, JavaConfig, ModpackInfo, PanelSettings, ServerConfig, SERVER_TYPES
from .properties import MANAGED_KEYS, read_properties, update_properties

router = APIRouter(prefix="/api")
manager: Manager  # injected by main


def M() -> Manager:
    return manager


def inst_or_404(sid: str) -> ServerInstance:
    try:
        return manager.get(sid)
    except KeyError:
        raise HTTPException(404, "Serwer nie istnieje")


def run_errors(fn):
    """Translate domain errors into HTTP 400s."""
    import functools

    @functools.wraps(fn)
    async def wrapper(*a, **kw):
        try:
            return await fn(*a, **kw)
        except (ServerError, modpacks.ModpackError, ValueError) as exc:
            raise HTTPException(400, str(exc))
        except ValidationError as exc:
            raise HTTPException(422, "; ".join(e["msg"] for e in exc.errors()))

    return wrapper


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text[:40] or "server"


def cf_key() -> str:
    return manager.settings.curseforge_api_key or CURSEFORGE_API_KEY_ENV


def container_memory_limit() -> int | None:
    for path in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        try:
            raw = Path(path).read_text().strip()
            if raw and raw != "max":
                v = int(raw)
                if v < 1 << 50:
                    return v
        except (OSError, ValueError):
            pass
    return None


# ====================================================================== auth

class Credentials(BaseModel):
    username: str
    password: str


def _set_cookie(resp: Response, token: str) -> None:
    resp.set_cookie(auth.COOKIE, token, max_age=auth.SESSION_TTL, httponly=True, samesite="strict",
                    secure=SECURE_COOKIES, path="/")


@router.get("/auth/state")
async def auth_state(request: Request):
    user = auth.verify_token(request.cookies.get(auth.COOKIE))
    return {"setup_required": auth.setup_required(), "user": user, "version": VERSION}


@router.post("/auth/setup")
async def auth_setup(body: Credentials, response: Response):
    if not auth.setup_required():
        raise HTTPException(403, "Panel jest już skonfigurowany")
    auth.create_user(body.username, body.password)
    _set_cookie(response, auth.make_token(body.username.strip()))
    return {"ok": True, "user": body.username.strip()}


@router.post("/auth/login")
async def auth_login(body: Credentials, request: Request, response: Response):
    ip = request.client.host if request.client else "?"
    token = await asyncio.to_thread(auth.login, body.username.strip(), body.password, ip)
    _set_cookie(response, token)
    return {"ok": True, "user": body.username.strip()}


@router.post("/auth/logout")
async def auth_logout(response: Response):
    response.delete_cookie(auth.COOKIE, path="/")
    return {"ok": True}


@router.get("/users")
async def list_users(_: str = Depends(auth.require_user)):
    return [{"username": u.username, "created_at": u.created_at} for u in auth.users().values()]


@router.post("/users")
async def add_user(body: Credentials, _: str = Depends(auth.require_user)):
    auth.create_user(body.username, body.password)
    return {"ok": True}


class PasswordBody(BaseModel):
    password: str


@router.put("/users/{username}/password")
async def change_password(username: str, body: PasswordBody, response: Response,
                          me: str = Depends(auth.require_user)):
    auth.set_password(username, body.password)
    if username == me:  # password change invalidates sessions – keep the current one alive
        _set_cookie(response, auth.make_token(me))
    return {"ok": True}


@router.delete("/users/{username}")
async def remove_user(username: str, me: str = Depends(auth.require_user)):
    if username == me:
        raise HTTPException(400, "Nie możesz usunąć własnego konta")
    auth.delete_user(username)
    return {"ok": True}


# ====================================================================== system

_disk_cache: dict[str, tuple[float, int]] = {}


def dir_size(path: Path) -> int:
    hit = _disk_cache.get(str(path))
    if hit and time.time() - hit[0] < 60:
        return hit[1]
    total = 0
    for dirpath, _d, fnames in os.walk(path):
        for f in fnames:
            with contextlib.suppress(OSError):
                total += os.lstat(os.path.join(dirpath, f)).st_size
    _disk_cache[str(path)] = (time.time(), total)
    return total


@router.get("/system")
async def system(_: str = Depends(auth.require_user)):
    vm = psutil.virtual_memory()
    du = shutil.disk_usage(DATA_DIR)
    limit = container_memory_limit()
    insts = list(manager.instances.values())
    return {
        "version": VERSION,
        "cpu_percent": psutil.cpu_percent(None),
        "cpu_count": psutil.cpu_count() or 1,
        "load": os.getloadavg() if hasattr(os, "getloadavg") else None,
        "mem_total": min(vm.total, limit) if limit else vm.total,
        "mem_used": vm.total - vm.available,
        "mem_host_total": vm.total,
        "container_limit": limit,
        "disk_total": du.total, "disk_used": du.used, "disk_free": du.free,
        "uptime": time.time() - psutil.boot_time(),
        "platform": f"{platform.system()} {platform.machine()}",
        "servers_total": len(insts),
        "servers_online": sum(1 for i in insts if i.state == "online"),
        "players_online": sum(len(i.online) for i in insts),
        "proxy": manager.proxy.stats() if manager.proxy else None,
        "voice": manager.voice.stats() if manager.voice else None,
        "bedrock": manager.bedrock.stats() if manager.bedrock else None,
    }


@router.get("/settings")
async def get_settings(_: str = Depends(auth.require_user)):
    s = manager.settings
    d = s.model_dump()
    d["curseforge_api_key"] = ""
    d["curseforge_configured"] = bool(cf_key())
    d["curseforge_from_env"] = bool(CURSEFORGE_API_KEY_ENV) and not s.curseforge_api_key
    d["proxy_port"] = PROXY_PORT
    d["defaults"] = {k: PanelSettings.model_fields[k].default for k in TEXT_SETTINGS}
    d["resource_pack_base"] = rpacks.public_base(manager)
    from .config import VOICE_PORT
    d["voice_port"] = VOICE_PORT
    d["bedrock_port"] = BEDROCK_PORT
    return d


TEXT_SETTINGS = ("public_panel_url", "pack_subdomain", "motd_offline", "motd_starting", "version_offline", "version_starting",
                 "kick_offline", "kick_starting", "kick_waking")


class SettingsBody(BaseModel):
    base_domain: Optional[str] = None
    default_server_id: Optional[str] = None
    curseforge_api_key: Optional[str] = None
    public_address: Optional[str] = None
    public_panel_url: Optional[str] = None
    pack_subdomain: Optional[str] = Field(default=None, max_length=63)
    motd_offline: Optional[str] = Field(default=None, max_length=300)
    motd_starting: Optional[str] = Field(default=None, max_length=300)
    version_offline: Optional[str] = Field(default=None, max_length=80)
    version_starting: Optional[str] = Field(default=None, max_length=80)
    kick_offline: Optional[str] = Field(default=None, max_length=500)
    kick_starting: Optional[str] = Field(default=None, max_length=500)
    kick_waking: Optional[str] = Field(default=None, max_length=500)


@router.put("/settings")
async def put_settings(body: SettingsBody, _: str = Depends(auth.require_user)):
    s = manager.settings
    if body.base_domain is not None:
        bd = body.base_domain.strip().lower().strip(".")
        if bd and not re.match(r"^[a-z0-9.-]+$", bd):
            raise HTTPException(400, "Niepoprawna domena")
        s.base_domain = bd
    if body.default_server_id is not None:
        s.default_server_id = body.default_server_id or None
    if body.curseforge_api_key is not None and body.curseforge_api_key != "":
        s.curseforge_api_key = "" if body.curseforge_api_key == "-" else body.curseforge_api_key.strip()
    if body.public_address is not None:
        s.public_address = body.public_address.strip()
    for key in TEXT_SETTINGS:
        val = getattr(body, key)
        if val is not None:
            if key == "public_panel_url" and val.strip() and not re.match(r"^https?://[^\s/]+(/.*)?$", val.strip()):
                raise HTTPException(400, "Publiczny adres panelu musi zaczynać się od http:// lub https://")
            if key == "pack_subdomain":
                val = val.strip().lower() or "packs"
                if not re.match(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$", val):
                    raise HTTPException(400, "Niepoprawna subdomena paczek zasobów")
            setattr(s, key, val.strip() if key == "public_panel_url" else val)
    manager.save_settings(s)
    rpacks.resync_urls(manager)
    if manager.bedrock:
        manager.bedrock.request_apply()
    return await get_settings("")


@router.get("/engines")
async def engines(_: str = Depends(auth.require_user)):
    return providers.ENGINES


@router.get("/versions/{engine}")
@run_errors
async def versions(engine: str, snapshots: bool = False, _: str = Depends(auth.require_user)):
    if engine not in SERVER_TYPES:
        raise HTTPException(404, "Nieznany silnik")
    return await providers.game_versions(engine, snapshots)


@router.get("/versions/{engine}/{mc}/loaders")
@run_errors
async def loader_versions(engine: str, mc: str, _: str = Depends(auth.require_user)):
    if engine not in SERVER_TYPES:
        raise HTTPException(404, "Nieznany silnik")
    return await providers.loader_versions(engine, mc)


@router.get("/java")
async def java_runtimes(_: str = Depends(auth.require_user)):
    runtimes = await asyncio.to_thread(javamod.installed_runtimes)
    used = {}
    for i in manager.instances.values():
        if i.java_feature:
            used.setdefault(i.java_feature, []).append(i.cfg.name)
    for r in runtimes:
        r["used_by"] = used.get(r["feature"], [])
    return {"runtimes": runtimes, "available": await javamod.available_releases()}


@router.post("/java/{feature}")
@run_errors
async def java_install(feature: int, _: str = Depends(auth.require_user)):
    feat, exe = await javamod.ensure_java(feature, lambda s: None)
    return {"feature": feat, "path": str(exe)}


@router.delete("/java/{feature}")
async def java_remove(feature: int, _: str = Depends(auth.require_user)):
    if any(i.running and i.java_feature == feature for i in manager.instances.values()):
        raise HTTPException(400, "Ta Java jest używana przez działający serwer")
    await asyncio.to_thread(javamod.remove_runtime, feature)
    return {"ok": True}


# ====================================================================== servers

@router.get("/servers")
async def list_servers(_: str = Depends(auth.require_user)):
    return sorted((i.summary() for i in manager.instances.values()), key=lambda s: s["created_at"])


class CreateServer(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    type: Literal["vanilla", "paper", "purpur", "fabric", "quilt", "forge", "neoforge"]
    mc_version: str
    loader_version: Optional[str] = None
    subdomain: Optional[str] = None
    java: JavaConfig = JavaConfig()
    eula: bool = False


def _unique_subdomain(wanted: str | None, name: str) -> str:
    base = slugify(wanted or name)
    taken = {i.cfg.subdomain for i in manager.instances.values()}
    if wanted:
        return base
    sub, n = base, 2
    while sub in taken:
        sub, n = f"{base}-{n}", n + 1
    return sub


@router.post("/servers")
@run_errors
async def create_server(body: CreateServer, _: str = Depends(auth.require_user)):
    if not body.eula:
        raise HTTPException(400, "Musisz zaakceptować EULA Minecrafta")
    inst = manager.create(name=body.name, type=body.type, mc_version=body.mc_version,
                          loader_version=body.loader_version or None,
                          subdomain=_unique_subdomain(body.subdomain, body.name), java=body.java.model_dump())
    await inst.install(eula=True)
    return inst.summary()


class CreateModpack(BaseModel):
    source: Literal["modrinth", "curseforge"]
    project_id: str
    version_id: str
    name: str = Field(min_length=1, max_length=64)
    subdomain: Optional[str] = None
    java: JavaConfig = JavaConfig()
    eula: bool = False
    icon_url: Optional[str] = None


@router.post("/servers/modpack")
@run_errors
async def create_from_modpack(body: CreateModpack, _: str = Depends(auth.require_user)):
    if not body.eula:
        raise HTTPException(400, "Musisz zaakceptować EULA Minecrafta")
    key = cf_key()
    if body.source == "curseforge" and not key:
        raise HTTPException(400, "Brak klucza API CurseForge – ustaw go w Ustawieniach.")
    info = ModpackInfo(source=body.source, project_id=body.project_id, version_id=body.version_id,
                       name=body.name, icon_url=body.icon_url)
    inst = manager.create(name=body.name, type="vanilla", mc_version="?", loader_version=None,
                          subdomain=_unique_subdomain(body.subdomain, body.name), java=body.java.model_dump(),
                          modpack=info.model_dump())

    async def prepare(log):
        if body.source == "modrinth":
            plan = await modpacks.modrinth_plan_prefer_server(body.version_id, cf_key(), log)
        else:
            plan = await modpacks.curseforge_plan_from_file(key, body.project_id, body.version_id, log)
        if inst.cfg.modpack:
            inst.cfg.modpack.version_name = plan.meta.get("version_name", "")
            inst.cfg.modpack.icon_url = inst.cfg.modpack.icon_url or plan.meta.get("icon_url")
        return plan

    await inst.install(eula=True, prepare=prepare)
    return inst.summary()


@router.post("/servers/modpack/upload")
@run_errors
async def create_from_upload(
    file: UploadFile = File(...), name: str = Form(...), subdomain: str = Form(""),
    max_ram_mb: int = Form(4096), eula: bool = Form(False), _: str = Depends(auth.require_user),
):
    if not eula:
        raise HTTPException(400, "Musisz zaakceptować EULA Minecrafta")
    dest = TMP_DIR / f"upload-{int(time.time() * 1000)}-{Path(file.filename or 'pack.zip').name}"
    with open(dest, "wb") as fh:
        await asyncio.to_thread(shutil.copyfileobj, file.file, fh, 1024 * 1024)
    if not zipfile.is_zipfile(dest):
        dest.unlink(missing_ok=True)
        raise HTTPException(400, "Plik nie jest archiwum ZIP/.mrpack")
    java = JavaConfig(max_ram_mb=max_ram_mb, min_ram_mb=min(1024, max_ram_mb))
    inst = manager.create(name=name, type="vanilla", mc_version="?", loader_version=None,
                          subdomain=_unique_subdomain(subdomain or None, name), java=java.model_dump(),
                          modpack=ModpackInfo(source="file", name=file.filename or name).model_dump())

    async def prepare(log):
        return modpacks.plan_from_upload(dest, cf_key(), log)

    await inst.install(eula=True, prepare=prepare)
    return inst.summary()


@router.get("/servers/{sid}")
async def get_server(sid: str, _: str = Depends(auth.require_user)):
    inst = inst_or_404(sid)
    return {**inst.summary(), "config": inst.cfg.model_dump()}


class ConfigPatch(BaseModel):
    name: Optional[str] = None
    subdomain: Optional[str] = None
    aliases: Optional[list[str]] = None
    auto_start: Optional[bool] = None
    auto_restart: Optional[bool] = None
    start_on_join: Optional[bool] = None
    proxy_protocol: Optional[bool] = None
    restart_daily_at: Optional[str] = None
    idle_shutdown_minutes: Optional[int] = Field(default=None, ge=0, le=24 * 60)
    java: Optional[JavaConfig] = None
    backup: Optional[BackupSchedule] = None


@router.patch("/servers/{sid}/config")
@run_errors
async def patch_config(sid: str, body: ConfigPatch, _: str = Depends(auth.require_user)):
    inst = inst_or_404(sid)
    data = inst.cfg.model_dump()
    patch = body.model_dump(exclude_unset=True)
    if "restart_daily_at" in patch and not patch["restart_daily_at"]:
        patch["restart_daily_at"] = None
    data.update(patch)
    new = ServerConfig.model_validate(data)
    if new.java.min_ram_mb > new.java.max_ram_mb:
        raise HTTPException(400, "Minimalny RAM nie może być większy niż maksymalny")
    manager.check_unique(new.subdomain, new.aliases, exclude=sid)
    inst.cfg = new
    manager.save_server(new)
    if manager.bedrock:
        manager.bedrock.request_apply()
    inst.broadcast({"type": "state", "state": inst.state, "summary": inst.summary()})
    return {**inst.summary(), "config": new.model_dump()}


@router.delete("/servers/{sid}")
@run_errors
async def delete_server(sid: str, _: str = Depends(auth.require_user)):
    await manager.delete(sid)
    return {"ok": True}


class PowerBody(BaseModel):
    action: Literal["start", "stop", "restart", "kill"]


@router.post("/servers/{sid}/power")
@run_errors
async def power(sid: str, body: PowerBody, _: str = Depends(auth.require_user)):
    inst = inst_or_404(sid)
    if body.action == "start":
        await inst.start()
    elif body.action == "stop":
        inst.spawn(inst.stop())
    elif body.action == "restart":
        inst.spawn(inst.restart())
    elif body.action == "kill":
        await inst.kill()
    return inst.summary()


class CommandBody(BaseModel):
    command: str = Field(max_length=2000)


@router.post("/servers/{sid}/command")
@run_errors
async def command(sid: str, body: CommandBody, _: str = Depends(auth.require_user)):
    await inst_or_404(sid).send_command(body.command)
    return {"ok": True}


class ReinstallBody(BaseModel):
    type: Optional[Literal["vanilla", "paper", "purpur", "fabric", "quilt", "forge", "neoforge"]] = None
    mc_version: Optional[str] = None
    loader_version: Optional[str] = None


@router.post("/servers/{sid}/reinstall")
@run_errors
async def reinstall(sid: str, body: ReinstallBody, _: str = Depends(auth.require_user)):
    inst = inst_or_404(sid)
    if inst.running:
        raise HTTPException(400, "Najpierw zatrzymaj serwer")
    if body.type:
        inst.cfg.type = body.type
    if body.mc_version:
        inst.cfg.mc_version = body.mc_version
    inst.cfg.loader_version = body.loader_version or None
    manager.save_server(inst.cfg)
    await inst.install(eula=True)  # EULA was accepted when the server was created
    return inst.summary()


@router.get("/servers/{sid}/stats")
async def stats(sid: str, _: str = Depends(auth.require_user)):
    inst = inst_or_404(sid)
    size = await asyncio.to_thread(dir_size, inst.dir) if inst.dir.exists() else 0
    return {"history": list(inst.stats), "disk": size, "cpu_count": psutil.cpu_count() or 1}


@router.get("/servers/{sid}/startup")
async def startup_info(sid: str, _: str = Depends(auth.require_user)):
    inst = inst_or_404(sid)
    required = inst.cfg.required_java or javamod.heuristic_java_for(inst.cfg.mc_version)
    feature = inst.cfg.java.version or await javamod.resolve_feature(required)
    try:
        cmd = inst.build_command("java", feature) if inst.cfg.launch else []
    except ServerError as exc:
        cmd = [f"# {exc}"]
    vm = psutil.virtual_memory()
    limit = container_memory_limit()
    return {
        "command": cmd, "required_java": required, "java_feature": feature,
        "available_java": await javamod.available_releases(),
        "installed_java": [r["feature"] for r in await asyncio.to_thread(javamod.installed_runtimes)],
        "mem_total": min(vm.total, limit) if limit else vm.total,
        "allocated_other": sum(i.cfg.java.max_ram_mb for i in manager.instances.values() if i.cfg.id != sid) * 1048576,
    }


@router.get("/servers/{sid}/properties")
async def get_properties(sid: str, _: str = Depends(auth.require_user)):
    inst = inst_or_404(sid)
    return {"properties": read_properties(inst.dir / "server.properties"), "managed": sorted(MANAGED_KEYS),
            "exists": (inst.dir / "server.properties").exists()}


class PropsBody(BaseModel):
    properties: dict[str, Any]
    replace: bool = False


@router.put("/servers/{sid}/properties")
async def put_properties(sid: str, body: PropsBody, _: str = Depends(auth.require_user)):
    inst = inst_or_404(sid)
    changes = {k: v for k, v in body.properties.items() if k not in MANAGED_KEYS and re.match(r"^[\w.\-]+$", k)}
    path = inst.dir / "server.properties"
    if body.replace:
        current = read_properties(path)
        for k in current:
            if k not in changes and k not in MANAGED_KEYS:
                changes[k] = None
    update_properties(path, changes)
    inst._enforce_properties()
    if manager.bedrock:
        manager.bedrock.request_apply()
    return {"properties": read_properties(path), "restart_required": inst.running}


@router.get("/servers/{sid}/icon")
async def server_icon(sid: str, _: str = Depends(auth.require_user)):
    inst = inst_or_404(sid)
    p = inst.dir / "server-icon.png"
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p, media_type="image/png", headers={"Cache-Control": "no-cache"})


# ====================================================================== players

@router.get("/servers/{sid}/players")
async def get_players(sid: str, _: str = Depends(auth.require_user)):
    inst = inst_or_404(sid)
    d = inst.dir
    whitelist = pl.read_list(d / "whitelist.json")
    ops = pl.read_list(d / "ops.json")
    data = inst.players_store.data
    week = time.time() - 7 * 86400
    pending = sorted(
        (a for a in data.get("attempts", {}).values()
         if a.get("ts", 0) > week and not pl.contains(whitelist, a["name"])),
        key=lambda a: a.get("ts", 0), reverse=True)
    known = sorted(data.get("known", {}).values(), key=lambda k: k.get("last_seen", 0), reverse=True)
    props = inst.props()
    return {
        "online": sorted(inst.online), "whitelist": whitelist, "ops": ops,
        "banned": pl.read_list(d / "banned-players.json"), "banned_ips": pl.read_list(d / "banned-ips.json"),
        "pending": pending, "known": known[:100],
        "whitelist_enabled": props.get("white-list", "false") == "true",
        "enforce_whitelist": props.get("enforce-whitelist", "false") == "true",
        "online_mode": props.get("online-mode", "true") == "true",
        "op_level_default": int(props.get("op-permission-level", "4") or 4),
        "running": inst.running,
    }


class NameBody(BaseModel):
    name: str
    level: int = Field(default=4, ge=1, le=4)
    reason: str = ""


async def _profile(inst: ServerInstance, name: str) -> dict:
    known = inst.players_store.data.get("known", {}).get(name.lower())
    if known and known.get("uuid"):
        return {"uuid": known["uuid"], "name": known["name"]}
    return await pl.lookup_profile(name, inst.online_mode)


@router.post("/servers/{sid}/players/whitelist")
@run_errors
async def whitelist_add(sid: str, body: NameBody, _: str = Depends(auth.require_user)):
    inst = inst_or_404(sid)
    prof = await _profile(inst, body.name)
    if inst.state == "online":
        await inst.send_command(f"whitelist add {prof['name']}")
        await asyncio.sleep(0.8)
        if not pl.contains(pl.read_list(inst.dir / "whitelist.json"), prof["name"]):
            pl.add_whitelist(inst.dir / "whitelist.json", prof)
            await inst.send_command("whitelist reload", echo=False)
    else:
        pl.add_whitelist(inst.dir / "whitelist.json", prof)
    return {"ok": True, "profile": prof}


@router.delete("/servers/{sid}/players/whitelist/{name}")
@run_errors
async def whitelist_remove(sid: str, name: str, _: str = Depends(auth.require_user)):
    inst = inst_or_404(sid)
    if inst.state == "online":
        await inst.send_command(f"whitelist remove {name}")
        await asyncio.sleep(0.5)
    pl.remove_by_name(inst.dir / "whitelist.json", name)
    if inst.state == "online":
        await inst.send_command("whitelist reload", echo=False)
    return {"ok": True}


class ToggleBody(BaseModel):
    enabled: bool
    enforce: Optional[bool] = None


@router.post("/servers/{sid}/players/whitelist/toggle")
@run_errors
async def whitelist_toggle(sid: str, body: ToggleBody, _: str = Depends(auth.require_user)):
    inst = inst_or_404(sid)
    changes = {"white-list": body.enabled}
    if body.enforce is not None:
        changes["enforce-whitelist"] = body.enforce
    update_properties(inst.dir / "server.properties", changes)
    if inst.state == "online":
        await inst.send_command("whitelist on" if body.enabled else "whitelist off")
    return {"ok": True}


@router.delete("/servers/{sid}/players/pending/{name}")
async def pending_dismiss(sid: str, name: str, _: str = Depends(auth.require_user)):
    inst = inst_or_404(sid)
    inst.players_store.data.get("attempts", {}).pop(name.lower(), None)
    inst.players_store.save()
    return {"ok": True}


@router.post("/servers/{sid}/players/ops")
@run_errors
async def op_add(sid: str, body: NameBody, _: str = Depends(auth.require_user)):
    inst = inst_or_404(sid)
    prof = await _profile(inst, body.name)
    default_level = int(inst.props().get("op-permission-level", "4") or 4)
    if inst.state == "online":
        # A running server keeps the op list in memory, so use the command (default op level).
        await inst.send_command(f"op {prof['name']}")
        if body.level != default_level:
            inst.panel_log(f"Nadano OP z poziomem domyślnym ({default_level}). Poziom {body.level} "
                           "można ustawić, gdy serwer jest wyłączony.")
    else:
        pl.add_op(inst.dir / "ops.json", prof, body.level)
    return {"ok": True, "profile": prof}


@router.delete("/servers/{sid}/players/ops/{name}")
@run_errors
async def op_remove(sid: str, name: str, _: str = Depends(auth.require_user)):
    inst = inst_or_404(sid)
    if inst.state == "online":
        await inst.send_command(f"deop {name}")
        await asyncio.sleep(0.5)
    pl.remove_by_name(inst.dir / "ops.json", name)
    return {"ok": True}


@router.post("/servers/{sid}/players/kick")
@run_errors
async def kick(sid: str, body: NameBody, _: str = Depends(auth.require_user)):
    inst = inst_or_404(sid)
    await inst.send_command(f"kick {body.name} {body.reason}".strip())
    return {"ok": True}


@router.post("/servers/{sid}/players/ban")
@run_errors
async def ban(sid: str, body: NameBody, _: str = Depends(auth.require_user)):
    inst = inst_or_404(sid)
    if inst.state == "online":
        await inst.send_command(f"ban {body.name} {body.reason}".strip())
    else:
        prof = await _profile(inst, body.name)
        items = pl.read_list(inst.dir / "banned-players.json")
        if not pl.contains(items, prof["name"]):
            items.append({"uuid": prof["uuid"], "name": prof["name"], "created": time.strftime("%Y-%m-%d %H:%M:%S +0000", time.gmtime()),
                          "source": "MineNode", "expires": "forever", "reason": body.reason or "Banned by an operator."})
            pl.write_list(inst.dir / "banned-players.json", items)
    return {"ok": True}


@router.delete("/servers/{sid}/players/bans/{name}")
@run_errors
async def pardon(sid: str, name: str, _: str = Depends(auth.require_user)):
    inst = inst_or_404(sid)
    if inst.state == "online":
        await inst.send_command(f"pardon {name}")
        await asyncio.sleep(0.5)
    pl.remove_by_name(inst.dir / "banned-players.json", name)
    return {"ok": True}


# ====================================================================== backups

@router.get("/servers/{sid}/backups")
async def backups_list(sid: str, _: str = Depends(auth.require_user)):
    inst = inst_or_404(sid)
    data = await manager.backups.list(sid)
    return {**data, "schedule": inst.cfg.backup.model_dump(), "busy": manager.backups.busy(sid),
            "last_backup_at": inst.cfg.last_backup_at,
            "destinations": [{"id": d.id, "name": d.name, "type": d.type} for d in manager.backups.destinations()]}


class BackupBody(BaseModel):
    note: str = Field(default="", max_length=200)
    destinations: Optional[list[str]] = None


@router.post("/servers/{sid}/backups")
@run_errors
async def backups_create(sid: str, body: BackupBody, _: str = Depends(auth.require_user)):
    inst = inst_or_404(sid)
    if manager.backups.busy(sid):
        raise HTTPException(409, "Kopia jest już w trakcie tworzenia")
    inst.spawn(manager.backups.create(inst, note=body.note, targets=body.destinations))
    return {"ok": True}


@router.post("/servers/{sid}/backups/{bid}/restore")
@run_errors
async def backups_restore(sid: str, bid: str, location: Optional[str] = None, _: str = Depends(auth.require_user)):
    inst = inst_or_404(sid)
    try:
        await manager.backups.restore(inst, bid, location)
    except FileNotFoundError:
        raise HTTPException(404, "Kopia nie istnieje")
    return {"ok": True}


@router.delete("/servers/{sid}/backups/{bid}")
@run_errors
async def backups_delete(sid: str, bid: str, location: Optional[str] = None, _: str = Depends(auth.require_user)):
    inst_or_404(sid)
    try:
        await manager.backups.delete(sid, bid, location)
    except (FileNotFoundError, KeyError):
        raise HTTPException(404, "Kopia nie istnieje")
    return {"ok": True}


@router.get("/servers/{sid}/backups/{bid}/download")
async def backups_download(sid: str, bid: str, location: Optional[str] = None, _: str = Depends(auth.require_user)):
    from . import storage as stg
    inst = inst_or_404(sid)
    try:
        dest = await manager.backups.locate(sid, bid, location)
    except (FileNotFoundError, ValueError):
        raise HTTPException(404, "Kopia nie istnieje")
    store = stg.build(dest)
    key = f"{sid}/{bid}.zip"
    name = f"{slugify(inst.cfg.name)}-{bid}.zip"
    local = store.local_path(key)
    if local:
        return FileResponse(local, filename=name, media_type="application/zip")
    return StreamingResponse(store.stream(key), media_type="application/zip",
                             headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.put("/servers/{sid}/backups/schedule")
async def backups_schedule(sid: str, body: BackupSchedule, _: str = Depends(auth.require_user)):
    inst = inst_or_404(sid)
    known = {d.id for d in manager.backups.destinations()}
    body.destinations = [d for d in body.destinations if d in known] or ["local"]
    inst.cfg.backup = body
    manager.save_server(inst.cfg)
    return body.model_dump()


# ====================================================================== backup destinations

def _mask(d) -> dict:
    out = d.model_dump()
    for f in storage_mod.SECRET_FIELDS:
        out[f] = ""
        out[f + "_set"] = bool(getattr(d, f))
    out["summary"] = storage_mod.describe(d)
    return out


@router.get("/backup-destinations")
async def destinations_list(_: str = Depends(auth.require_user)):
    used: dict[str, list[str]] = {}
    for i in manager.instances.values():
        for did in i.cfg.backup.destinations:
            used.setdefault(did, []).append(i.cfg.name)
    return [{**_mask(d), "used_by": used.get(d.id, [])} for d in manager.backups.destinations()]


class DestinationBody(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    type: Literal["folder", "s3", "webdav"]
    path: str = ""
    endpoint: str = ""
    region: str = ""
    bucket: str = ""
    prefix: str = "minenode"
    access_key: str = ""
    secret_key: str = ""
    path_style: bool = False
    url: str = ""
    username: str = ""
    password: str = ""


def _build_dest(body: DestinationBody, did: str):
    existing = next((d for d in manager.settings.backup_destinations if d.id == did), None)
    data = body.model_dump()
    data["endpoint"] = data["endpoint"].strip().rstrip("/")
    data["url"] = data["url"].strip()
    data["path"] = data["path"].strip()
    for f in storage_mod.SECRET_FIELDS:  # empty secret on edit = keep the stored one
        if not data[f] and existing is not None:
            data[f] = getattr(existing, f)
    from .models import BackupDestination
    dest = BackupDestination(id=did, **data)
    if dest.type == "folder":
        storage_mod.validate_folder(dest.path)
    if dest.type == "s3" and not dest.bucket:
        raise storage_mod.StorageError("Podaj nazwę bucketu")
    if dest.type == "s3" and dest.endpoint and not dest.endpoint.startswith(("http://", "https://")):
        raise storage_mod.StorageError("Endpoint S3 musi zaczynać się od http:// lub https://")
    if dest.type == "webdav" and not dest.url.startswith(("http://", "https://")):
        raise storage_mod.StorageError("Adres WebDAV musi zaczynać się od http:// lub https://")
    return dest


@router.post("/backup-destinations/test")
async def destinations_test(body: DestinationBody, id: Optional[str] = None, _: str = Depends(auth.require_user)):
    try:
        dest = _build_dest(body, id or "test")
        started = time.time()
        await storage_mod.build(dest).test()
        return {"ok": True, "ms": int((time.time() - started) * 1000)}
    except (storage_mod.StorageError, ValueError, ValidationError) as exc:
        return {"ok": False, "error": str(exc)}


@router.post("/backup-destinations/{did}/test")
async def destinations_test_saved(did: str, _: str = Depends(auth.require_user)):
    try:
        dest = manager.backups.destination(did)
    except KeyError:
        raise HTTPException(404, "Miejsce kopii nie istnieje")
    try:
        started = time.time()
        await storage_mod.build(dest).test()
        return {"ok": True, "ms": int((time.time() - started) * 1000)}
    except (storage_mod.StorageError, ValueError, OSError) as exc:
        return {"ok": False, "error": str(exc)}


@router.post("/backup-destinations")
async def destinations_create(body: DestinationBody, _: str = Depends(auth.require_user)):
    try:
        dest = _build_dest(body, uuid.uuid4().hex[:8])
    except (storage_mod.StorageError, ValueError) as exc:
        raise HTTPException(400, str(exc))
    s = manager.settings
    s.backup_destinations.append(dest)
    manager.save_settings(s)
    return _mask(dest)


@router.put("/backup-destinations/{did}")
async def destinations_update(did: str, body: DestinationBody, _: str = Depends(auth.require_user)):
    s = manager.settings
    idx = next((i for i, d in enumerate(s.backup_destinations) if d.id == did), None)
    if idx is None:
        raise HTTPException(404, "Miejsce kopii nie istnieje")
    try:
        dest = _build_dest(body, did)
    except (storage_mod.StorageError, ValueError) as exc:
        raise HTTPException(400, str(exc))
    s.backup_destinations[idx] = dest
    manager.save_settings(s)
    for inst in manager.instances.values():
        manager.backups.invalidate(inst.cfg.id)
    return _mask(dest)


@router.delete("/backup-destinations/{did}")
async def destinations_delete(did: str, _: str = Depends(auth.require_user)):
    s = manager.settings
    if not any(d.id == did for d in s.backup_destinations):
        raise HTTPException(404, "Miejsce kopii nie istnieje")
    s.backup_destinations = [d for d in s.backup_destinations if d.id != did]
    manager.save_settings(s)
    for inst in manager.instances.values():  # servers fall back to the panel disk
        if did in inst.cfg.backup.destinations:
            inst.cfg.backup.destinations = [x for x in inst.cfg.backup.destinations if x != did] or ["local"]
            manager.save_server(inst.cfg)
    return {"ok": True}


# ====================================================================== files

@router.get("/servers/{sid}/files")
async def files_list(sid: str, path: str = "", _: str = Depends(auth.require_user)):
    inst = inst_or_404(sid)
    inst.dir.mkdir(parents=True, exist_ok=True)
    return await asyncio.to_thread(files.list_dir, inst.dir, path)


@router.get("/servers/{sid}/files/content")
async def files_read(sid: str, path: str, _: str = Depends(auth.require_user)):
    inst = inst_or_404(sid)
    p = files.resolve(inst.dir, path)
    if not p.is_file():
        raise HTTPException(404, "Plik nie istnieje")
    if p.stat().st_size > files.TEXT_LIMIT:
        raise HTTPException(413, "Plik jest zbyt duży do edycji w przeglądarce – pobierz go")
    if not files.is_text(p):
        raise HTTPException(415, "To plik binarny – można go tylko pobrać")
    return {"path": path, "content": p.read_text(encoding="utf-8", errors="replace"), "mtime": p.stat().st_mtime}


class WriteBody(BaseModel):
    path: str
    content: str


@router.put("/servers/{sid}/files/content")
async def files_write(sid: str, body: WriteBody, _: str = Depends(auth.require_user)):
    inst = inst_or_404(sid)
    p = files.resolve(inst.dir, body.path)
    if p.is_dir():
        raise HTTPException(400, "To jest folder")
    p.parent.mkdir(parents=True, exist_ok=True)
    from .store import atomic_write_text
    atomic_write_text(p, body.content)
    return {"ok": True}


@router.post("/servers/{sid}/files/upload")
async def files_upload(sid: str, path: str = Form(""), files_: list[UploadFile] = File(..., alias="files"),
                       _: str = Depends(auth.require_user)):
    inst = inst_or_404(sid)
    target_dir = files.resolve(inst.dir, path)
    target_dir.mkdir(parents=True, exist_ok=True)
    saved = []
    for up in files_:
        rel_name = (up.filename or "file").replace("\\", "/").lstrip("/")
        dest = (target_dir / rel_name).resolve()
        if not dest.is_relative_to(inst.dir.resolve()):
            raise HTTPException(400, "Niepoprawna nazwa pliku")
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "wb") as fh:
            await asyncio.to_thread(shutil.copyfileobj, up.file, fh, 1024 * 1024)
        saved.append(rel_name)
    return {"ok": True, "saved": saved}


class PathBody(BaseModel):
    path: str


@router.post("/servers/{sid}/files/mkdir")
async def files_mkdir(sid: str, body: PathBody, _: str = Depends(auth.require_user)):
    p = files.resolve(inst_or_404(sid).dir, body.path)
    p.mkdir(parents=True, exist_ok=True)
    return {"ok": True}


class RenameBody(BaseModel):
    src: str
    dst: str


@router.post("/servers/{sid}/files/rename")
async def files_rename(sid: str, body: RenameBody, _: str = Depends(auth.require_user)):
    inst = inst_or_404(sid)
    src, dst = files.resolve(inst.dir, body.src), files.resolve(inst.dir, body.dst)
    if not src.exists():
        raise HTTPException(404, "Plik nie istnieje")
    if dst.exists():
        raise HTTPException(409, "Plik o tej nazwie już istnieje")
    dst.parent.mkdir(parents=True, exist_ok=True)
    src.rename(dst)
    return {"ok": True}


class PathsBody(BaseModel):
    paths: list[str]


@router.post("/servers/{sid}/files/delete")
async def files_delete(sid: str, body: PathsBody, _: str = Depends(auth.require_user)):
    inst = inst_or_404(sid)
    root = inst.dir.resolve()
    for rel in body.paths:
        p = files.resolve(inst.dir, rel)
        if p == root:
            raise HTTPException(400, "Nie można usunąć katalogu głównego")
        if p.is_dir() and not p.is_symlink():
            await asyncio.to_thread(shutil.rmtree, p)
        else:
            p.unlink(missing_ok=True)
    return {"ok": True}


@router.post("/servers/{sid}/files/extract")
async def files_extract(sid: str, body: PathBody, _: str = Depends(auth.require_user)):
    inst = inst_or_404(sid)
    p = files.resolve(inst.dir, body.path)
    if not p.is_file() or not zipfile.is_zipfile(p):
        raise HTTPException(400, "To nie jest archiwum ZIP")

    def work():
        with zipfile.ZipFile(p) as zf:
            safe_extract(zf, p.parent)

    try:
        await asyncio.to_thread(work)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True}


@router.get("/servers/{sid}/files/download")
async def files_download(sid: str, path: str, _: str = Depends(auth.require_user)):
    inst = inst_or_404(sid)
    p = files.resolve(inst.dir, path)
    if p.is_file():
        return FileResponse(p, filename=p.name)
    if p.is_dir():
        name = f"{slugify(inst.cfg.name)}-{p.name or 'server'}.zip"
        return StreamingResponse(files.stream_zip([p], p.parent), media_type="application/zip",
                                 headers={"Content-Disposition": f'attachment; filename="{name}"'})
    raise HTTPException(404, "Plik nie istnieje")


@router.get("/servers/{sid}/files/archive")
async def files_archive(sid: str, _: str = Depends(auth.require_user)):
    """Download every server file as a single ZIP (streamed)."""
    inst = inst_or_404(sid)
    inst.dir.mkdir(parents=True, exist_ok=True)
    name = f"{slugify(inst.cfg.name)}-{time.strftime('%Y%m%d-%H%M')}.zip"
    return StreamingResponse(files.stream_zip([inst.dir], inst.dir), media_type="application/zip",
                             headers={"Content-Disposition": f'attachment; filename="{name}"'})


# ====================================================================== modpack browsing

@router.get("/modpacks/search")
@run_errors
async def modpack_search(source: Literal["modrinth", "curseforge"] = "modrinth", q: str = "", offset: int = 0,
                         mc: str | None = None, _: str = Depends(auth.require_user)):
    if source == "modrinth":
        return await modpacks.modrinth_search(q, offset, mc=mc or None)
    return await modpacks.curseforge_search(cf_key(), q, offset, mc=mc or None)


@router.get("/modpacks/server-pack")
@run_errors
async def modpack_server_pack(version_id: str, _: str = Depends(auth.require_user)):
    """Does an official server pack exist for this Modrinth version (on CurseForge)?"""
    return await modpacks.server_pack_for_modrinth(version_id, cf_key())


@router.get("/modpacks/{source}/{project_id}/versions")
@run_errors
async def modpack_versions(source: Literal["modrinth", "curseforge"], project_id: str,
                           _: str = Depends(auth.require_user)):
    if source == "modrinth":
        return await modpacks.modrinth_versions(project_id)
    return await modpacks.curseforge_files(cf_key(), project_id)


# ====================================================================== websocket

@router.websocket("/servers/{sid}/ws")
async def server_ws(ws: WebSocket, sid: str):
    user = auth.ws_user(ws)
    inst = manager.instances.get(sid)
    if not user or not inst:
        await ws.close(code=4401 if not user else 4404)
        return
    await ws.accept()
    q = inst.subscribe()
    try:
        await ws.send_json({"type": "init", "summary": inst.summary(), "console": list(inst.console)[-1500:],
                            "stats": list(inst.stats), "job": inst.job})

        async def sender():
            while True:
                first = await q.get()
                batch = [first]
                while not q.empty() and len(batch) < 300:
                    batch.append(q.get_nowait())
                await ws.send_json({"type": "batch", "messages": batch} if len(batch) > 1 else first)

        async def receiver():
            while True:
                msg = await ws.receive_json()
                if msg.get("type") == "command" and isinstance(msg.get("command"), str):
                    try:
                        await inst.send_command(msg["command"][:2000])
                    except ServerError as exc:
                        await ws.send_json({"type": "error", "message": str(exc)})

        tasks = [asyncio.create_task(sender()), asyncio.create_task(receiver())]
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
        for t in done:
            with contextlib.suppress(Exception):
                t.result()
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        inst.unsubscribe(q)


# ====================================================================== mods & plugins

@router.get("/servers/{sid}/content")
@run_errors
async def content_list(sid: str, check: bool = True, _: str = Depends(auth.require_user)):
    inst = inst_or_404(sid)
    data = await content.inventory(inst, check=check)
    data["engine"] = inst.cfg.type
    data["running"] = inst.running
    return data


@router.get("/servers/{sid}/content/search")
@run_errors
async def content_search(sid: str, source: Literal["modrinth", "curseforge"] = "modrinth", q: str = "",
                         offset: int = 0, any_version: bool = False, _: str = Depends(auth.require_user)):
    inst = inst_or_404(sid)
    kind = content.kind_for(inst.cfg.type)
    if not kind:
        raise HTTPException(400, "Serwer Vanilla nie obsługuje modów ani pluginów – zmień silnik w Ustawieniach.")
    mc = None if any_version else inst.cfg.mc_version
    if source == "modrinth":
        res = await content.modrinth_search(kind, q, offset, mc, content.LOADER_COMPAT[inst.cfg.type])
    else:
        res = await content.curseforge_search(cf_key(), kind, q, offset, mc, inst.cfg.type)
    installed = {i.get("project_id") for i in content.load_meta(inst)["files"].values()}
    for it in res["items"]:
        it["installed"] = it["id"] in installed
    res["kind"] = kind
    return res


class ContentInstall(BaseModel):
    source: Literal["modrinth", "curseforge"]
    project_id: str
    version_id: Optional[str] = None


@router.post("/servers/{sid}/content/install")
@run_errors
async def content_install(sid: str, body: ContentInstall, _: str = Depends(auth.require_user)):
    inst = inst_or_404(sid)
    ins = content.Installer(inst, cf_key())
    if body.source == "modrinth":
        await ins.modrinth(body.project_id, body.version_id)
    else:
        if not cf_key():
            raise HTTPException(400, "Brak klucza API CurseForge – ustaw go w Ustawieniach.")
        await ins.curseforge(body.project_id, body.version_id)
    for i in ins.installed:
        inst.panel_log(f"Zainstalowano {i['title']} {i['version'] or ''}" + (" (zależność)" if i["dependency"] else ""))
    return {"installed": ins.installed, "restart_required": inst.running}


class FileBody(BaseModel):
    file: str
    enabled: bool = True


@router.post("/servers/{sid}/content/update")
@run_errors
async def content_update(sid: str, body: FileBody, _: str = Depends(auth.require_user)):
    inst = inst_or_404(sid)
    data = await content.inventory(inst, check=True)
    item = next((i for i in data["items"] if i["file"] == body.file), None)
    if not item or not item.get("update"):
        raise HTTPException(400, "Brak dostępnej aktualizacji")
    ins = content.Installer(inst, cf_key())
    await ins.modrinth(item["project_id"], item["update"]["version_id"])
    if body.file not in content.load_meta(inst)["files"]:  # old file not tracked → remove it explicitly
        content.remove(inst, body.file)
    return {"installed": ins.installed, "restart_required": inst.running}


@router.post("/servers/{sid}/content/update-all")
@run_errors
async def content_update_all(sid: str, _: str = Depends(auth.require_user)):
    inst = inst_or_404(sid)
    data = await content.inventory(inst, check=True)
    done = []
    for item in [i for i in data["items"] if i.get("update")]:
        ins = content.Installer(inst, cf_key())
        try:
            await ins.modrinth(item["project_id"], item["update"]["version_id"])
            if item["file"] not in content.load_meta(inst)["files"]:
                content.remove(inst, item["file"])
            done += ins.installed
        except Exception as exc:  # noqa: BLE001
            inst.panel_log(f"Aktualizacja {item['title']} nieudana: {exc}")
    return {"installed": done, "restart_required": inst.running}


@router.post("/servers/{sid}/content/prune-client")
@run_errors
async def content_prune_client(sid: str, _: str = Depends(auth.require_user)):
    from . import clientmods
    inst = inst_or_404(sid)
    disabled = await clientmods.prune(inst.dir, inst.panel_log)
    return {"disabled": disabled, "restart_required": inst.running and bool(disabled)}


@router.post("/servers/{sid}/content/toggle")
@run_errors
async def content_toggle(sid: str, body: FileBody, _: str = Depends(auth.require_user)):
    inst = inst_or_404(sid)
    content.toggle(inst, body.file, body.enabled)
    return {"ok": True, "restart_required": inst.running}


@router.delete("/servers/{sid}/content/{file}")
@run_errors
async def content_delete(sid: str, file: str, _: str = Depends(auth.require_user)):
    inst = inst_or_404(sid)
    content.remove(inst, file)
    return {"ok": True, "restart_required": inst.running}


# ====================================================================== resource packs

@router.get("/servers/{sid}/resourcepacks")
async def rp_list(sid: str, _: str = Depends(auth.require_user)):
    inst = inst_or_404(sid)
    props = read_properties(inst.dir / "server.properties")
    prompt = props.get("resource-pack-prompt", "")
    try:
        import json as _json
        prompt = _json.loads(prompt).get("text", prompt) if prompt.startswith("{") else prompt
    except ValueError:
        pass
    packs = rpacks.load_index(inst)
    for p in packs:
        p["url"] = rpacks.pack_url(manager, inst, p)
    return {"packs": packs, "active": rpacks.active_id(inst, manager), "base_url": rpacks.public_base(manager),
            "explicit_base": bool(manager.settings.public_panel_url),
            "require": props.get("require-resource-pack", "false") == "true", "prompt": prompt,
            "external_url": props.get("resource-pack", "") if not rpacks.active_id(inst, manager) else "",
            "running": inst.running}


@router.post("/servers/{sid}/resourcepacks/upload")
@run_errors
async def rp_upload(sid: str, file: UploadFile = File(...), name: str = Form(""), _: str = Depends(auth.require_user)):
    inst = inst_or_404(sid)
    tmp = TMP_DIR / f"rp-upload-{int(time.time() * 1000)}.zip"
    with open(tmp, "wb") as fh:
        await asyncio.to_thread(shutil.copyfileobj, file.file, fh, 1024 * 1024)
    label = name.strip() or Path(file.filename or "paczka.zip").stem
    pack = await asyncio.to_thread(rpacks.add_file, inst, tmp, label, "upload")
    return pack


class RpInstall(BaseModel):
    source: Literal["modrinth", "curseforge"]
    project_id: str
    version_id: Optional[str] = None


@router.post("/servers/{sid}/resourcepacks/install")
@run_errors
async def rp_install(sid: str, body: RpInstall, _: str = Depends(auth.require_user)):
    inst = inst_or_404(sid)
    if body.source == "modrinth":
        return await rpacks.install_modrinth(inst, body.project_id, body.version_id)
    if not cf_key():
        raise HTTPException(400, "Brak klucza API CurseForge – ustaw go w Ustawieniach.")
    return await rpacks.install_curseforge(inst, cf_key(), body.project_id)


@router.get("/servers/{sid}/resourcepacks/search")
@run_errors
async def rp_search(sid: str, source: Literal["modrinth", "curseforge"] = "modrinth", q: str = "", offset: int = 0,
                    any_version: bool = False, _: str = Depends(auth.require_user)):
    inst = inst_or_404(sid)
    mc = None if any_version else inst.cfg.mc_version
    if source == "modrinth":
        return await content.modrinth_search("resourcepack", q, offset, mc, [])
    return await content.curseforge_search(cf_key(), "resourcepack", q, offset, mc, inst.cfg.type)


class RpActivate(BaseModel):
    require: bool = False
    prompt: str = Field(default="", max_length=300)


@router.post("/servers/{sid}/resourcepacks/{pid}/activate")
@run_errors
async def rp_activate(sid: str, pid: str, body: RpActivate, _: str = Depends(auth.require_user)):
    inst = inst_or_404(sid)
    try:
        pack = rpacks.activate(inst, manager, pid, body.require, body.prompt)
    except FileNotFoundError:
        raise HTTPException(404, "Paczka nie istnieje")
    return {"ok": True, "pack": pack, "restart_required": inst.running}


@router.post("/servers/{sid}/resourcepacks/deactivate")
async def rp_deactivate(sid: str, _: str = Depends(auth.require_user)):
    inst = inst_or_404(sid)
    rpacks.deactivate(inst)
    return {"ok": True, "restart_required": inst.running}


@router.delete("/servers/{sid}/resourcepacks/{pid}")
async def rp_delete(sid: str, pid: str, _: str = Depends(auth.require_user)):
    inst = inst_or_404(sid)
    rpacks.delete(inst, manager, pid)
    return {"ok": True}


@router.get("/servers/{sid}/resourcepacks/{pid}/icon")
async def rp_icon(sid: str, pid: str, _: str = Depends(auth.require_user)):
    inst = inst_or_404(sid)
    try:
        data = await asyncio.to_thread(rpacks.icon, inst, pid)
    except (FileNotFoundError, zipfile.BadZipFile):
        data = None
    if not data:
        raise HTTPException(404)
    return Response(data, media_type="image/png", headers={"Cache-Control": "max-age=3600"})


@router.get("/servers/{sid}/resourcepacks/{pid}/download")
async def rp_download(sid: str, pid: str, _: str = Depends(auth.require_user)):
    inst = inst_or_404(sid)
    try:
        pack, path = rpacks.get(inst, pid)
    except FileNotFoundError:
        raise HTTPException(404)
    return FileResponse(path, filename=f"{slugify(pack['name'])}.zip", media_type="application/zip")


# ====================================================================== public (no auth) – resource pack hosting

public_router = APIRouter()


@public_router.get("/rp/{sid}/{token}/{pid}.zip", include_in_schema=False)
async def rp_public(sid: str, token: str, pid: str):
    inst = manager.instances.get(sid)
    if not inst:
        raise HTTPException(404)
    try:
        pack, path = rpacks.get(inst, pid)
    except FileNotFoundError:
        raise HTTPException(404)
    import hmac as _hmac
    if not _hmac.compare_digest(pack["token"], token) or not path.exists():
        raise HTTPException(404)
    return FileResponse(path, media_type="application/zip", filename=f"{pid}.zip",
                        headers={"Cache-Control": "public, max-age=86400"})


# ====================================================================== Simple Voice Chat

def _voice_state(inst: ServerInstance) -> dict:
    from .config import VOICE_PORT
    supported = content.kind_for(inst.cfg.type) is not None
    vp = manager.voice
    host = voice.public_host(inst)
    return {
        "supported": supported, "installed": supported and voice.is_installed(inst),
        "enabled": inst.cfg.voice_enabled, "internal_port": inst.cfg.voice_port, "public_port": VOICE_PORT,
        "public_host": host, "voice_host": f"{host}:{VOICE_PORT}" if host else None,
        "config_file": str(voice.config_path(inst).relative_to(inst.dir)),
        "proxy_listening": bool(vp and vp.transport), "proxy_error": vp.error if vp else None,
        "sessions": (vp.stats()["per_server"].get(inst.cfg.id, 0) if vp else 0), "running": inst.running,
    }


@router.get("/servers/{sid}/voice")
async def voice_get(sid: str, _: str = Depends(auth.require_user)):
    return _voice_state(inst_or_404(sid))


class VoiceBody(BaseModel):
    enabled: bool


@router.post("/servers/{sid}/voice")
@run_errors
async def voice_set(sid: str, body: VoiceBody, _: str = Depends(auth.require_user)):
    inst = inst_or_404(sid)
    if body.enabled and not content.kind_for(inst.cfg.type):
        raise HTTPException(400, "Simple Voice Chat wymaga silnika z modami lub pluginami")
    inst.cfg.voice_enabled = body.enabled
    if body.enabled and not inst.cfg.voice_port:
        inst.cfg.voice_port = manager.allocate_voice_port()
    manager.save_server(inst.cfg)
    if body.enabled:
        from .config import VOICE_PORT
        voice.write_config(inst, VOICE_PORT)
    return {**_voice_state(inst), "restart_required": inst.running}


@router.post("/servers/{sid}/voice/install")
@run_errors
async def voice_install(sid: str, _: str = Depends(auth.require_user)):
    inst = inst_or_404(sid)
    ins = content.Installer(inst, cf_key())
    await ins.modrinth(voice.SVC_PROJECT_ID)
    if not inst.cfg.voice_enabled:
        inst.cfg.voice_enabled = True
        inst.cfg.voice_port = inst.cfg.voice_port or manager.allocate_voice_port()
        manager.save_server(inst.cfg)
    from .config import VOICE_PORT
    voice.write_config(inst, VOICE_PORT)
    return {**_voice_state(inst), "installed_files": ins.installed, "restart_required": inst.running}


# ====================================================================== Bedrock Edition (Geyser)

def _bedrock_state(inst: ServerInstance) -> dict:
    bp = manager.bedrock
    hosts = bp.hosts_for(inst) if bp else []
    host = hosts[0] if hosts else None
    display = f"[{host}]" if host and ":" in host else host
    address = None
    if display:
        address = display if BEDROCK_PORT == 19132 else f"{display}:{BEDROCK_PORT}"
    stats = bp.stats() if bp else {}
    return {
        "enabled": inst.cfg.bedrock_enabled,
        "supported": True,
        "public_host": host,
        "public_port": BEDROCK_PORT,
        "address": address,
        "hostnames": hosts,
        "gateway_running": bool(bp and bp.running),
        "gateway_listening": bool(bp and bp.listening),
        "gateway_error": bp.error if bp else None,
        "gateway_log": stats.get("log", []),
        "gateway_version": stats.get("version"),
        "conflicts": stats.get("conflicts", []),
        "running": inst.running,
    }


@router.get("/servers/{sid}/bedrock")
async def bedrock_get(sid: str, _: str = Depends(auth.require_user)):
    return _bedrock_state(inst_or_404(sid))


class BedrockBody(BaseModel):
    enabled: bool


@router.post("/servers/{sid}/bedrock")
@run_errors
async def bedrock_set(sid: str, body: BedrockBody, _: str = Depends(auth.require_user)):
    inst = inst_or_404(sid)
    inst.cfg.bedrock_enabled = body.enabled
    manager.save_server(inst.cfg)
    if manager.bedrock:
        manager.bedrock.request_apply()
    inst.broadcast({"type": "state", "state": inst.state, "summary": inst.summary()})
    return _bedrock_state(inst)
