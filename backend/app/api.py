"""REST + WebSocket API."""
from __future__ import annotations

import asyncio
import contextlib
import logging
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
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile, WebSocket
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, ValidationError
from starlette.background import BackgroundTask
from starlette.websockets import WebSocketDisconnect

from . import storage as storage_mod
from . import bedrock_compat, datapacks as dpacks, packs, worldgen
from . import auth, auth_sessions, content, files, java as javamod, modpacks, players as pl, providers, resourcepacks as rpacks, voice
from . import chunky, lod, playerstats, squaremap, webmap
from .models import ChunkySettings
from .backups import safe_extract
from .config import BEDROCK_PORT, DATA_DIR, PROXY_PORT, SECURE_COOKIES, TMP_DIR, VERSION, CURSEFORGE_API_KEY_ENV
from .manager import Manager, ServerError, ServerInstance
from .models import BackupSchedule, HOSTNAME_RE, JavaConfig, ModpackInfo, PanelSettings, PublicPageSettings, ServerConfig, ServerType, SERVER_TYPES
from .properties import MANAGED_KEYS, read_properties, update_properties

router = APIRouter(prefix="/api")
manager: Manager  # injected by main
log = logging.getLogger("minenode")


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
        except (ServerError, modpacks.ModpackError, packs.PackError, worldgen.WorldgenError, ValueError) as exc:
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
async def auth_logout(request: Request, response: Response):
    auth.revoke_token(request.cookies.get(auth.COOKIE))
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


# ====================================================================== player authentication

@router.post("/player-auth/lobby/update")
async def player_auth_lobby_update(_: str = Depends(auth.require_user)):
    """Check the author's repository for a newer PicoLimbo now (normally every few hours) and build it.

    A build takes minutes, so it continues in the background; the answer comes back after a few seconds
    with ``building: true`` and the panel follows the progress in the lobby status."""
    lobby = getattr(manager, "auth_lobby", None)
    if lobby is None:
        raise HTTPException(503, "Lobby autoryzacji nie działa")
    if lobby.updater.building:
        return {"updated": False, "building": True, "running": lobby.running, "error": lobby.error,
                "picolimbo": lobby.updater.status()}
    task = lobby._manual_update = asyncio.create_task(lobby.check_for_update(force=True))  # keep a reference
    with contextlib.suppress(asyncio.TimeoutError):
        await asyncio.wait_for(asyncio.shield(task), 4)
    changed = task.done() and not task.cancelled() and task.result()
    return {"updated": bool(changed), "building": not task.done(), "running": lobby.running, "error": lobby.error,
            "picolimbo": lobby.updater.status()}


@router.get("/player-auth/sessions")
async def player_auth_sessions(q: str = "", _: str = Depends(auth.require_user)):
    """Active game logins. Passwords and hashes are never returned."""
    return auth_sessions.sessions(q)


@router.get("/player-auth/sessions/{name}")
async def player_auth_session(name: str, _: str = Depends(auth.require_user)):
    """One active session with the captured client fingerprint (read-only)."""
    session = next((item for item in auth_sessions.sessions()
                    if item["name"].lower() == name.lower()), None)
    if session is None:
        raise HTTPException(404, "Nie znaleziono sesji gracza")
    return {
        "session": session,
        "fingerprint": auth_sessions.fingerprint(session["name"], session["ip"]),
    }


@router.delete("/player-auth/sessions/{name}")
async def player_auth_logout(name: str, _: str = Depends(auth.require_user)):
    if not auth_sessions.clear_session(name):
        raise HTTPException(404, "Nie znaleziono sesji gracza")
    return {"ok": True}


@router.get("/player-auth/accounts")
async def player_auth_accounts(q: str = "", _: str = Depends(auth.require_user)):
    """Registered game accounts (without password material)."""
    return auth_sessions.accounts(q)


@router.get("/player-auth/accounts/{name}")
async def player_auth_account(name: str, _: str = Depends(auth.require_user)):
    detail = auth_sessions.account_detail(name)
    if detail is None:
        raise HTTPException(404, "Nie znaleziono zarejestrowanego gracza")
    return detail


class PlayerPasswordBody(BaseModel):
    password: str = Field(min_length=auth_sessions.PASSWORD_MIN_LENGTH, max_length=128)


@router.put("/player-auth/accounts/{name}/password")
async def player_auth_set_password(name: str, body: PlayerPasswordBody,
                                   _: str = Depends(auth.require_user)):
    try:
        ok = auth_sessions.set_password(name, body.password)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if not ok:
        raise HTTPException(404, "Nie znaleziono zarejestrowanego gracza")
    return {"ok": True}


@router.delete("/player-auth/accounts/{name}")
async def player_auth_delete_account(name: str, _: str = Depends(auth.require_user)):
    if not auth_sessions.delete_account(name):
        raise HTTPException(404, "Nie znaleziono zarejestrowanego gracza")
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
    d["domains"] = s.domains()
    d["defaults"] = {k: PanelSettings.model_fields[k].default for k in TEXT_SETTINGS}
    d["resource_pack_base"] = rpacks.public_base(manager)
    from .config import VOICE_PORT
    d["voice_port"] = VOICE_PORT
    d["bedrock_port"] = BEDROCK_PORT
    lobby = getattr(manager, "auth_lobby", None)
    d["auth_lobby"] = {
        "enabled": True,
        "running": bool(lobby and lobby.running),
        "error": lobby.error if lobby else None,
        "port": getattr(lobby, "port", None),
        "session_minutes": 30,
        "picolimbo": lobby.updater.status() if lobby else None,
    }
    return d


TEXT_SETTINGS = ("public_panel_url", "pack_subdomain", "motd_offline", "motd_starting", "version_offline", "version_starting",
                 "kick_offline", "kick_starting", "kick_waking")


class SettingsBody(BaseModel):
    base_domain: Optional[str] = None
    extra_domains: Optional[list[str]] = Field(default=None, max_length=50)
    default_server_id: Optional[str] = None
    curseforge_api_key: Optional[str] = None
    public_address: Optional[str] = None
    public_panel_url: Optional[str] = None
    pack_subdomain: Optional[str] = Field(default=None, max_length=63)
    bedrock_advertise: Optional[str] = Field(default=None, max_length=300)
    motd_offline: Optional[str] = Field(default=None, max_length=300)
    motd_starting: Optional[str] = Field(default=None, max_length=300)
    version_offline: Optional[str] = Field(default=None, max_length=80)
    version_starting: Optional[str] = Field(default=None, max_length=80)
    kick_offline: Optional[str] = Field(default=None, max_length=500)
    kick_starting: Optional[str] = Field(default=None, max_length=500)
    kick_waking: Optional[str] = Field(default=None, max_length=500)


def _clean_domain(raw: str) -> str:
    d = (raw or "").strip().lower().strip(".")
    if d.startswith("*."):
        d = d[2:]
    if d and not HOSTNAME_RE.match(d):
        raise HTTPException(400, f"Niepoprawna domena: {raw}")
    return d


@router.put("/settings")
async def put_settings(body: SettingsBody, _: str = Depends(auth.require_user)):
    s = manager.settings
    if body.base_domain is not None:
        s.base_domain = _clean_domain(body.base_domain)
    if body.extra_domains is not None:
        extra = []
        for raw in body.extra_domains:
            d = _clean_domain(raw)
            if d and d != s.base_domain and d not in extra:
                extra.append(d)
        s.extra_domains = extra
    if not s.base_domain and s.extra_domains:  # the first domain on the list becomes the default one
        s.base_domain = s.extra_domains.pop(0)
    if body.default_server_id is not None:
        s.default_server_id = body.default_server_id or None
    if body.curseforge_api_key is not None and body.curseforge_api_key != "":
        s.curseforge_api_key = "" if body.curseforge_api_key == "-" else body.curseforge_api_key.strip()
    if body.public_address is not None:
        s.public_address = body.public_address.strip()
    if body.bedrock_advertise is not None:
        parts = [p.strip() for p in body.bedrock_advertise.split(",") if p.strip()]
        for p in parts:
            if not re.match(r"^(\[[0-9a-fA-F:]+\]|[A-Za-z0-9.-]+)(:\d{1,5})?$", p):
                raise HTTPException(400, f"Niepoprawny adres Bedrock: {p}")
        s.bedrock_advertise = ", ".join(parts)
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


class WorldgenSpec(BaseModel):
    level_type: str = Field(default="", max_length=100)  # normal / flat / large_biomes / amplified / namespaced id
    seed: str = Field(default="", max_length=100)
    generator: str = Field(default="", max_length=100)  # bukkit.yml worlds.<level>.generator (plugins)
    files: list[str] = Field(default_factory=list, max_length=worldgen.MAX_FILES)  # tokens from /worldgen/stage


class CreateSettings(BaseModel):
    """The most important settings, chosen in the wizard and written before the first start."""
    online_mode: bool = True
    max_players: int = Field(default=20, ge=1, le=1000)
    whitelist: bool = False
    auth_enabled: bool = True  # MineNodeAuth/PicoLimbo login when online-mode is off
    bedrock: bool = False
    gamemode: Literal["survival", "creative", "adventure", "spectator"] = "survival"
    difficulty: Literal["peaceful", "easy", "normal", "hard"] = "easy"
    hardcore: bool = False
    pvp: bool = True
    allow_flight: bool = False
    view_distance: int = Field(default=10, ge=2, le=32)
    start_on_join: bool = False
    idle_shutdown_minutes: int = Field(default=0, ge=0, le=24 * 60)


def _bedrock_check(engine: str, mc_version: str, online_mode: bool) -> dict:
    """Bedrock compatibility of a server that does not exist yet (same rules as for an existing one)."""
    from types import SimpleNamespace
    fake = SimpleNamespace(cfg=SimpleNamespace(type=engine, mc_version=mc_version), online_mode=online_mode,
                           dir=TMP_DIR / "nonexistent-bedrock-check", running=False, started_at=0)
    bp = manager.bedrock
    return bedrock_compat.quick_state(fake, bp.geyser_java_version if bp else None)


def _apply_create_settings(inst: ServerInstance, st: "CreateSettings") -> None:
    update_properties(inst.dir / "server.properties", {
        "online-mode": st.online_mode, "max-players": st.max_players,
        "white-list": st.whitelist, "enforce-whitelist": st.whitelist,
        "gamemode": st.gamemode, "difficulty": st.difficulty, "hardcore": st.hardcore, "pvp": st.pvp,
        "allow-flight": st.allow_flight, "view-distance": st.view_distance,
    })
    inst.cfg.auth_enabled = st.auth_enabled
    inst.cfg.start_on_join = st.start_on_join
    inst.cfg.idle_shutdown_minutes = st.idle_shutdown_minutes
    manager.save_server(inst.cfg)


async def _enable_bedrock_after_create(inst: ServerInstance) -> None:
    """Install the Geyser translator (if needed) and turn Bedrock on; a failure only logs."""
    bp = manager.bedrock
    try:
        await bedrock_compat.ensure(inst, bp.geyser_java_version if bp else None)
        inst.cfg.bedrock_enabled = True
        manager.save_server(inst.cfg)
        if bp:
            bp.request_apply()
        inst.panel_log("Bedrock Edition włączony – gracze Bedrock wejdą tym samym adresem.")
        inst.broadcast({"type": "state", "state": inst.state, "summary": inst.summary()})
    except Exception as exc:  # noqa: BLE001
        inst.panel_log(f"Nie udało się włączyć Bedrocka: {exc} – spróbuj w Ustawieniach serwera.")


@router.get("/bedrock/check")
async def bedrock_check(engine: str, mc: str, online_mode: bool = False, _: str = Depends(auth.require_user)):
    if engine not in SERVER_TYPES:
        raise HTTPException(404, "Nieznany silnik")
    state = _bedrock_check(engine, mc, online_mode)
    return {"supported": state.get("supported", False), "state": state.get("state"), "reason": state.get("reason")}


class CreateServer(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    type: ServerType
    mc_version: str
    loader_version: Optional[str] = None
    subdomain: Optional[str] = None
    domain: Optional[str] = None  # one of the panel's main domains ("" / None = the default one)
    java: JavaConfig = JavaConfig()
    eula: bool = False
    worldgen: Optional[WorldgenSpec] = None
    settings: Optional[CreateSettings] = None


def _set_domain(inst: ServerInstance, domain: str | None) -> None:
    """Remember the main domain chosen when creating a server (ignored if it is not configured)."""
    d = (domain or "").strip().lower().strip(".")
    domains = manager.settings.domains()
    if d and d in domains and d != domains[0]:
        inst.cfg.domain = d
        manager.save_server(inst.cfg)


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
    spec = body.worldgen.model_dump() if body.worldgen else None
    # Checked before anything is created: a bad generator file must not leave a half-made server behind.
    files = worldgen.validate(spec, body.type, body.mc_version) if spec else []
    st = body.settings
    if st and st.bedrock:
        check = _bedrock_check(body.type, body.mc_version, st.online_mode)
        if not check.get("supported"):
            raise HTTPException(400, f"Bedrock: {check.get('reason') or 'ten serwer nie jest zgodny z Geyserem'}")
    inst = manager.create(name=body.name, type=body.type, mc_version=body.mc_version,
                          loader_version=body.loader_version or None,
                          subdomain=_unique_subdomain(body.subdomain, body.name), java=body.java.model_dump())
    _set_domain(inst, body.domain)
    if st:
        await asyncio.to_thread(_apply_create_settings, inst, st)
    if spec:
        await asyncio.to_thread(worldgen.apply, inst, spec, files, inst.panel_log)
    await inst.install(eula=True)
    if st and st.bedrock:
        inst.spawn(_enable_bedrock_after_create(inst))
    return inst.summary()


@router.get("/worldgen/modrinth")
@run_errors
async def worldgen_search(engine: str, mc: str = "", q: str = Query("", max_length=100), offset: int = 0,
                          _: str = Depends(auth.require_user)):
    if engine not in SERVER_TYPES:
        raise HTTPException(404, "Nieznany silnik")
    return await worldgen.modrinth_search(engine, mc or None, q, offset)


class WorldgenModrinthBody(BaseModel):
    project_id: str = Field(max_length=20)
    engine: ServerType
    mc_version: str = Field(max_length=40)


@router.post("/worldgen/modrinth")
@run_errors
async def worldgen_from_modrinth(body: WorldgenModrinthBody, _: str = Depends(auth.require_user)):
    """Download a world generator from Modrinth for a server that is being created."""
    return await worldgen.stage_from_modrinth(body.project_id, body.engine, body.mc_version)


@router.post("/worldgen/stage")
@run_errors
async def worldgen_stage(file: UploadFile = File(...), _: str = Depends(auth.require_user)):
    """Upload one world generator file (datapack, plugin or mod) for a server that is being created."""
    worldgen.STAGE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = worldgen.STAGE_DIR / f"upload-{uuid.uuid4().hex}.part"
    try:
        with open(tmp, "wb") as fh:
            size = 0
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > worldgen.MAX_FILE:
                    raise HTTPException(400, "Plik jest za duży (maks. 512 MB)")
                fh.write(chunk)
        return await asyncio.to_thread(worldgen.stage, tmp, file.filename or "")
    finally:
        tmp.unlink(missing_ok=True)


@router.get("/servers/{sid}/export")
async def export_server(sid: str, _: str = Depends(auth.require_user)):
    """Download a complete, portable server package."""
    inst = inst_or_404(sid)
    try:
        path = await asyncio.to_thread(packs.create, inst)
    except packs.PackError as exc:
        raise HTTPException(400, str(exc)) from exc
    return FileResponse(path, filename=f"{inst.cfg.subdomain}.minenodepack",
                        media_type="application/octet-stream",
                        background=BackgroundTask(path.unlink, missing_ok=True))


@router.post("/servers/import")
@run_errors
async def import_server(
    file: UploadFile = File(...), name: str = Form(""), subdomain: str = Form(""),
    _: str = Depends(auth.require_user),
):
    """Import a complete server into a new instance."""
    filename = Path(file.filename or "server.minenodepack").name
    if not filename.lower().endswith(".minenodepack"):
        raise HTTPException(400, "Wybierz plik z rozszerzeniem .minenodepack")
    dest = TMP_DIR / f"import-{int(time.time() * 1000)}-{filename}"
    try:
        with open(dest, "wb") as fh:
            await asyncio.to_thread(shutil.copyfileobj, file.file, fh, 1024 * 1024)
        await asyncio.to_thread(packs.validate, dest)
        inst = await asyncio.to_thread(packs.import_pack, dest, manager, name.strip() or None,
                                       subdomain.strip() or None)
        return inst.summary()
    finally:
        dest.unlink(missing_ok=True)


class CreateModpack(BaseModel):
    source: Literal["modrinth", "curseforge"]
    project_id: str
    version_id: str
    name: str = Field(min_length=1, max_length=64)
    subdomain: Optional[str] = None
    domain: Optional[str] = None  # one of the panel's main domains ("" / None = the default one)
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
    _set_domain(inst, body.domain)

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
    file: UploadFile = File(...), name: str = Form(...), subdomain: str = Form(""), domain: str = Form(""),
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
    _set_domain(inst, domain)

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
    auth_enabled: Optional[bool] = None
    domain: Optional[str] = Field(default=None, max_length=253)  # main domain shown in the address ("" = default)
    custom_jar: Optional[str] = Field(default=None, max_length=255)  # "Custom JAR" servers: file to start


@router.patch("/servers/{sid}/config")
@run_errors
async def patch_config(sid: str, body: ConfigPatch, _: str = Depends(auth.require_user)):
    inst = inst_or_404(sid)
    data = inst.cfg.model_dump()
    patch = body.model_dump(exclude_unset=True)
    if "restart_daily_at" in patch and not patch["restart_daily_at"]:
        patch["restart_daily_at"] = None
    if "domain" in patch:
        domain = (patch["domain"] or "").strip().lower().strip(".")
        if domain and domain not in manager.settings.domains():
            raise HTTPException(400, f"Domeny {domain} nie ma na liście domen głównych panelu")
        # The default domain is stored as "none chosen", so it follows when the default changes.
        patch["domain"] = domain if domain and domain != manager.settings.domains()[0] else None
    if "custom_jar" in patch:
        jar = patch.pop("custom_jar") or ""
        if inst.cfg.type != "custom":
            raise HTTPException(400, "Plik .jar wybiera się tylko dla silnika Custom JAR")
        if jar and jar not in inst.custom_jars():
            raise HTTPException(400, f"Nie ma pliku {jar} w głównym folderze serwera")
        data["launch"] = {"kind": "jar", "target": jar}
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
    type: Optional[ServerType] = None
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


ICON_MAX_BYTES = 64 * 1024  # the proxy only embeds icons up to this size for offline servers


def validate_server_icon(data: bytes) -> None:
    """Minecraft only shows server-icon.png when it is a PNG of exactly 64×64 pixels."""
    if len(data) > ICON_MAX_BYTES:
        raise HTTPException(400, "Ikona jest za duża (maks. 64 KB)")
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        raise HTTPException(400, "Ikona musi być plikiem PNG")
    if (int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")) != (64, 64):
        raise HTTPException(400, "Ikona musi mieć dokładnie 64×64 piksele")


def _icon_state(inst: ServerInstance) -> dict:
    return {"has_icon": (inst.dir / "server-icon.png").exists(), "icon_version": inst.icon_version(),
            "restart_required": inst.running}


@router.post("/servers/{sid}/icon")
async def upload_server_icon(sid: str, file: UploadFile = File(...), _: str = Depends(auth.require_user)):
    inst = inst_or_404(sid)
    data = await file.read(ICON_MAX_BYTES + 1)
    validate_server_icon(data)
    await asyncio.to_thread(inst.set_icon, data)
    return _icon_state(inst)


@router.post("/servers/{sid}/icon/default")
async def reset_server_icon(sid: str, _: str = Depends(auth.require_user)):
    inst = inst_or_404(sid)
    await asyncio.to_thread(inst.remove_icon)
    await asyncio.to_thread(inst.ensure_default_icon)
    return _icon_state(inst)


@router.delete("/servers/{sid}/icon")
async def delete_server_icon(sid: str, _: str = Depends(auth.require_user)):
    inst = inst_or_404(sid)
    await asyncio.to_thread(inst.remove_icon)
    return _icon_state(inst)


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


@router.get("/servers/{sid}/players/ping")
async def get_player_pings(sid: str, _: str = Depends(auth.require_user)):
    """Ping (TCP round-trip time, ms) of every online player; measured only when the panel asks.

    ``None`` means no measurement: the player is not connected through the proxy (e.g. Bedrock
    through Geyser, or a direct connection to the internal port)."""
    inst = inst_or_404(sid)
    measured = manager.proxy.player_pings(sid) if manager.proxy else {}
    return {"pings": {name: measured.get(name.lower()) for name in sorted(inst.online)}}


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

WS_SESSION_CHECK = 15  # seconds between session re-checks of an open console


@router.websocket("/servers/{sid}/ws")
async def server_ws(ws: WebSocket, sid: str):
    user = auth.ws_user(ws)
    inst = manager.instances.get(sid)
    if not user or not inst:
        await ws.close(code=4401 if not user else 4404)
        return
    token = ws.cookies.get(auth.COOKIE)

    def session_ok() -> bool:
        # Logout elsewhere, a password change or a deleted account must end an open console too.
        return auth.verify_token(token) == user

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
                if not session_ok():
                    return
                if msg.get("type") == "command" and isinstance(msg.get("command"), str):
                    try:
                        await inst.send_command(msg["command"][:2000])
                    except ServerError as exc:
                        await ws.send_json({"type": "error", "message": str(exc)})

        async def watchdog():
            while session_ok():
                await asyncio.sleep(WS_SESSION_CHECK)

        tasks = [asyncio.create_task(sender()), asyncio.create_task(receiver()), asyncio.create_task(watchdog())]
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
        for t in done:
            with contextlib.suppress(Exception):
                t.result()
        if not session_ok():
            with contextlib.suppress(Exception):
                await ws.close(code=4401)  # the client stops reconnecting on 4401
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


# ====================================================================== datapacks

@router.get("/servers/{sid}/datapacks")
async def dp_list(sid: str, _: str = Depends(auth.require_user)):
    inst = inst_or_404(sid)
    return {"packs": await asyncio.to_thread(dpacks.list_packs, inst), "level": dpacks.level_name(inst),
            "running": inst.running}


@router.post("/servers/{sid}/datapacks/upload")
@run_errors
async def dp_upload(sid: str, file: UploadFile = File(...), _: str = Depends(auth.require_user)):
    inst = inst_or_404(sid)
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    tmp = TMP_DIR / f"dp-upload-{uuid.uuid4().hex}.zip"
    size = 0
    with open(tmp, "wb") as fh:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > dpacks.MAX_SIZE:
                fh.close()
                tmp.unlink(missing_ok=True)
                raise HTTPException(413, "Plik jest za duży (limit 512 MB)")
            fh.write(chunk)
    pack = await asyncio.to_thread(dpacks.add_file, inst, tmp, file.filename or "datapack.zip")
    return pack | {"restart_required": inst.running}


class DpInstall(BaseModel):
    project_id: str = Field(min_length=1, max_length=64)


@router.post("/servers/{sid}/datapacks/install")
@run_errors
async def dp_install(sid: str, body: DpInstall, _: str = Depends(auth.require_user)):
    inst = inst_or_404(sid)
    return await dpacks.install_modrinth(inst, body.project_id) | {"restart_required": inst.running}


@router.get("/servers/{sid}/datapacks/search")
@run_errors
async def dp_search(sid: str, q: str = Query("", max_length=100), offset: int = 0, any_version: bool = False,
                    _: str = Depends(auth.require_user)):
    inst = inst_or_404(sid)
    return await content.modrinth_search("datapack", q, offset, None if any_version else inst.cfg.mc_version, [])


@router.post("/servers/{sid}/datapacks/toggle")
@run_errors
async def dp_toggle(sid: str, body: FileBody, _: str = Depends(auth.require_user)):
    inst = inst_or_404(sid)
    try:
        await asyncio.to_thread(dpacks.toggle, inst, body.file, body.enabled)
    except FileNotFoundError:
        raise HTTPException(404, "Datapack nie istnieje")
    return {"ok": True, "restart_required": inst.running}


@router.delete("/servers/{sid}/datapacks/{file}")
@run_errors
async def dp_delete(sid: str, file: str, _: str = Depends(auth.require_user)):
    inst = inst_or_404(sid)
    try:
        await asyncio.to_thread(dpacks.remove, inst, file)
    except FileNotFoundError:
        raise HTTPException(404, "Datapack nie istnieje")
    return {"ok": True, "restart_required": inst.running}


@router.get("/servers/{sid}/datapacks/{file}/icon")
async def dp_icon(sid: str, file: str, _: str = Depends(auth.require_user)):
    inst = inst_or_404(sid)
    try:
        data = await asyncio.to_thread(dpacks.icon, inst, file)
    except (FileNotFoundError, dpacks.DatapackError):
        data = None
    if not data:
        raise HTTPException(404)
    return Response(data, media_type="image/png", headers={"Cache-Control": "max-age=3600"})


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


# ====================================================================== public server page & squaremap map

def _public_inst(key: str) -> ServerInstance:
    """Resolve a public /<key>/stats path by id, subdomain or a slug of the server name.

    Subdomains are unique, so they win over name slugs when both happen to match.
    """
    raw = key.strip().lower()
    for inst in manager.instances.values():
        if inst.cfg.id == key:
            return inst
    for inst in manager.instances.values():
        if inst.cfg.subdomain.lower() == raw:
            return inst
    for inst in manager.instances.values():
        if slugify(inst.cfg.name) == raw:
            return inst
    raise HTTPException(404, "Serwer nie istnieje")


def _public_max_players(inst: ServerInstance) -> int:
    value = inst.props().get("max-players", "20")
    return int(value) if value.isdigit() else 20


def _public_stats(inst: ServerInstance) -> dict:
    props = inst.props()
    # The panel may have started before the server wrote its logs (or the log appeared later):
    # read the last conversation lazily so the public page still shows chat even while the server
    # is running (refresh_chat_history clears and rebuilds the buffer, so no message is duplicated).
    if not getattr(inst, "chat", None):
        refresh = getattr(inst, "refresh_chat_history", None)
        if refresh:
            refresh()
    known = inst.players_store.data.get("known", {})
    online_names = sorted(inst.online, key=str.lower)
    online_keys = {name.lower() for name in online_names}
    deaths = playerstats.death_counts(inst, known, online_names)
    online = []
    proxy = getattr(manager, "proxy", None)
    pings = proxy.player_pings(inst.cfg.id) if proxy else {}  # measured now; None for Bedrock/loopback
    for name in online_names:
        entry = known.get(name.lower(), {})
        online.append({"name": name, "uuid": entry.get("uuid"), "ping": pings.get(name.lower()),
                       "joined_at": inst.online.get(name), "last_seen": entry.get("last_seen"),
                       "deaths": deaths.get(name.lower(), 0)})
    offline = [
        {"name": entry.get("name", key), "uuid": entry.get("uuid"), "last_seen": entry.get("last_seen") or 0,
         "deaths": deaths.get(key, 0)}
        for key, entry in known.items() if key not in online_keys
    ]
    offline.sort(key=lambda p: p["last_seen"], reverse=True)
    map_ready = webmap.map_available(inst)
    page = getattr(inst.cfg, "public_page", None) or PublicPageSettings()
    return {
        "server": {
            "id": inst.cfg.id, "name": inst.cfg.name, "subdomain": inst.cfg.subdomain,
            "type": inst.cfg.type, "mc_version": inst.cfg.mc_version,
            "loader_version": inst.cfg.loader_version, "state": inst.state, "running": inst.running,
            "address": inst.address(), "motd": props.get("motd", "A Minecraft Server"),
            "max_players": _public_max_players(inst), "online_count": len(inst.online),
            "online_mode": inst.online_mode, "started_at": inst.started_at if inst.running else None,
            "uptime": int(time.time() - inst.started_at) if inst.running and inst.started_at else None,
            "icon": (f"/api/public/server-icon/{inst.cfg.id}?v={inst.icon_version()}"
                     if (inst.dir / "server-icon.png").is_file() else None),
            "whitelist": props.get("white-list", "false") == "true",
            "stats_supported": playerstats.supported(inst),
        },
        # Hidden lists are emptied here, not only in the browser: a disabled section must not leak data.
        "online": online if page.online_players else [],
        "offline": offline[:200] if page.recent_players else [],
        "chat": list(getattr(inst, "chat", [])) if page.chat else [],
        "players": {"online": len(online), "offline": len(offline), "known": len(online) + len(offline),
                    "max": _public_max_players(inst)},
        "public_page": page.model_dump(),
        "lod": _public_lod(inst),
        "map": {
            "enabled": inst.cfg.webmap.enabled,
            "provider": inst.cfg.webmap.provider,
            "title": webmap.INFO[inst.cfg.webmap.provider]["title"],
            "needs_running": webmap.INFO[inst.cfg.webmap.provider]["proxy"],
            "available": map_ready,
            "url": webmap.map_url(inst) if map_ready else None,
        },
    }


def _public_lod(inst: ServerInstance) -> dict:
    """Which distant-terrain mods players can use here (only what the server really offers)."""
    cfg = getattr(inst.cfg, "lod", None)
    mods = [key for key in ("distant_horizons", "voxy") if cfg and getattr(cfg, key) and lod.supported(inst, key)]
    return {"mods": mods}


@public_router.get("/api/public/servers/{key}/stats")
async def public_server_stats(key: str):
    inst = _public_inst(key)
    page = getattr(inst.cfg, "public_page", None) or PublicPageSettings()
    if not page.enabled:
        return JSONResponse({"detail": "Publiczna strona jest wyłączona."}, status_code=403,
                            headers={"Cache-Control": "no-store"})
    return JSONResponse(_public_stats(inst), headers={"Cache-Control": "no-store"})


@public_router.post("/api/public/servers/{key}/start")
async def public_server_start(key: str):
    """Start a stopped server on behalf of a visitor – only if the owner allowed it.

    There is no login here, so the button is gated by the same page switch plus its own flag.
    Repeated clicks are harmless: a running (or already starting) server is left alone.
    """
    inst = _public_inst(key)
    page = getattr(inst.cfg, "public_page", None) or PublicPageSettings()
    if not page.enabled or not page.start_button:
        return JSONResponse({"detail": "Uruchamianie serwera z tej strony jest wyłączone."}, status_code=403,
                            headers={"Cache-Control": "no-store"})
    if inst.running or inst.state in ("starting", "stopping", "installing"):
        return JSONResponse({"ok": True, "state": inst.state, "started": False},
                            headers={"Cache-Control": "no-store"})
    try:
        await inst.start()
    except ServerError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=400, headers={"Cache-Control": "no-store"})
    return JSONResponse({"ok": True, "state": inst.state, "started": True}, headers={"Cache-Control": "no-store"})


@public_router.get("/api/public/servers/{key}/players/{player_uuid}/stats")
async def public_player_stats(key: str, player_uuid: str):
    """World statistics of one player (1.13+), shown when a visitor clicks a name."""
    inst = _public_inst(key)
    page = getattr(inst.cfg, "public_page", None) or PublicPageSettings()
    if not page.enabled or not page.player_stats:
        return JSONResponse({"detail": "Statystyki graczy są wyłączone."}, status_code=403,
                            headers={"Cache-Control": "no-store"})
    if not playerstats.supported(inst):
        return JSONResponse({"detail": "Statystyki są dostępne tylko dla Minecrafta 1.13 lub nowszego."},
                            status_code=400, headers={"Cache-Control": "no-store"})
    canonical = playerstats.norm_uuid(player_uuid)
    if not canonical:
        raise HTTPException(404, "Gracz nie istnieje")
    # World files are written on save; an online player would otherwise show data up to a few
    # minutes old. Throttled, because /save-all is not free and visitors may click around.
    if inst.running and inst.state == "online" and time.time() - getattr(inst, "_stats_save_at", 0) > 15:
        inst._stats_save_at = time.time()
        with contextlib.suppress(Exception):
            await inst.send_command("save-all", echo=False)
            await asyncio.sleep(0.35)
    data = await asyncio.to_thread(playerstats.player_stats, inst, canonical)
    if not data:
        log.info("Brak statystyk gracza %s na serwerze %s (skanowane katalogi: %s, level-name: %s)",
                 canonical, getattr(inst.cfg, "id", "?"), playerstats.stats_dirs(inst), playerstats.level_name(inst))
        raise HTTPException(404, "Brak statystyk tego gracza")
    return JSONResponse(data, headers={"Cache-Control": "no-store"})


@public_router.get("/api/public/server-icon/{sid}")
async def public_server_icon(sid: str):
    inst = manager.instances.get(sid)
    if not inst:
        raise HTTPException(404)
    path = inst.dir / "server-icon.png"
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "no-cache"})


_PROXY_HEADERS = ("content-type", "content-encoding", "cache-control", "etag", "last-modified", "expires")


@public_router.get("/map/{sid}/{path:path}", include_in_schema=False)
async def webmap_public(sid: str, path: str, request: Request):
    """The live web map. squaremap's generated web directory is served from disk; Dynmap and BlueMap run
    their own web servers on a loopback port and are proxied here (GET only – no web chat or logins)."""
    inst = manager.instances.get(sid)
    if not inst or not inst.cfg.webmap.enabled:
        raise HTTPException(404)
    port = webmap.proxy_port(inst)
    if port:
        return await _proxy_map(inst, port, path, request)
    root = webmap.web_dir(inst).resolve()
    rel = path or "index.html"
    if rel.endswith("/"):
        rel += "index.html"
    target = (root / rel).resolve()
    if not root.is_dir() or not target.is_relative_to(root) or not target.is_file():
        raise HTTPException(404)
    headers = {"X-Frame-Options": "SAMEORIGIN"}
    if rel.startswith("assets/"):
        headers["Cache-Control"] = "public, max-age=31536000, immutable"
    else:
        # Tile changes and the JSON documents that describe them are polled live by the map.
        headers["Cache-Control"] = "no-cache"
    return FileResponse(target, headers=headers)


async def _proxy_map(inst: ServerInstance, port: int, path: str, request: Request):
    import httpx
    if not inst.running:
        return JSONResponse({"detail": "Mapa jest dostępna, gdy serwer działa."}, status_code=503,
                            headers={"Cache-Control": "no-store"})
    if ".." in path.split("/"):
        raise HTTPException(404)
    url = f"http://127.0.0.1:{port}/{path}"
    fwd = {k: v for k, v in request.headers.items()
           if k.lower() in ("accept", "accept-encoding", "if-none-match", "if-modified-since", "range")}
    client = httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=3.0))
    try:
        req = client.build_request("GET", url, params=request.query_params, headers=fwd)
        upstream = await client.send(req, stream=True)
    except httpx.HTTPError:
        await client.aclose()
        return JSONResponse({"detail": "Mapa jeszcze się uruchamia – spróbuj za chwilę."}, status_code=503,
                            headers={"Cache-Control": "no-store"})
    headers = {k: v for k, v in upstream.headers.items() if k.lower() in _PROXY_HEADERS}
    headers["X-Frame-Options"] = "SAMEORIGIN"

    async def close():
        await upstream.aclose()
        await client.aclose()

    # Raw bytes: BlueMap serves pre-compressed tiles with Content-Encoding, which must reach the browser as is.
    return StreamingResponse(upstream.aiter_raw(), status_code=upstream.status_code, headers=headers,
                             background=BackgroundTask(close))


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


# ====================================================================== public stats page

@router.get("/servers/{sid}/public-page")
async def public_page_get(sid: str, _: str = Depends(auth.require_user)):
    inst = inst_or_404(sid)
    return {**inst.cfg.public_page.model_dump(), "public_url": squaremap.public_url(inst)}


class PublicPageBody(BaseModel):
    enabled: Optional[bool] = None
    online_players: Optional[bool] = None
    join_guide: Optional[bool] = None
    chat: Optional[bool] = None
    recent_players: Optional[bool] = None
    online_count: Optional[bool] = None
    history_players: Optional[bool] = None
    start_button: Optional[bool] = None
    player_stats: Optional[bool] = None


@router.post("/servers/{sid}/public-page")
async def public_page_set(sid: str, body: PublicPageBody, _: str = Depends(auth.require_user)):
    inst = inst_or_404(sid)
    data = inst.cfg.public_page.model_dump()
    data.update(body.model_dump(exclude_none=True))
    inst.cfg.public_page = PublicPageSettings.model_validate(data)
    manager.save_server(inst.cfg)
    return {**inst.cfg.public_page.model_dump(), "public_url": squaremap.public_url(inst)}


# ====================================================================== web map (squaremap / Dynmap / BlueMap)

@router.get("/servers/{sid}/webmap")
async def webmap_get(sid: str, refresh: bool = False, _: str = Depends(auth.require_user)):
    return await webmap.state(inst_or_404(sid), refresh=refresh)


class WebMapBody(BaseModel):
    enabled: Optional[bool] = None
    provider: Optional[Literal["squaremap", "dynmap", "bluemap"]] = None
    show_players: Optional[bool] = None
    render_3d: Optional[bool] = None
    update: bool = False  # install the newest build now


@router.post("/servers/{sid}/webmap")
@run_errors
async def webmap_set(sid: str, body: WebMapBody, _: str = Depends(auth.require_user)):
    inst = inst_or_404(sid)
    wm = inst.cfg.webmap
    before = wm.model_copy()
    if body.provider is not None:
        wm.provider = body.provider
    if body.show_players is not None:
        wm.show_players = body.show_players
    if body.render_3d is not None:
        wm.render_3d = body.render_3d
    if body.enabled is not None:
        wm.enabled = body.enabled
    if wm.enabled and not webmap.engine_supported(inst, wm.provider):
        inst.cfg.webmap = before
        raise HTTPException(400, webmap.unsupported_reason(inst, wm.provider) or "Ta mapa nie działa na tym silniku")
    if wm.provider != before.provider and inst._map_render is not None and inst.map_render.running:
        inst.cfg.webmap = before
        raise HTTPException(400, "Poczekaj, aż skończy się renderowanie mapy, albo je przerwij")
    result = None
    try:
        if wm.enabled and webmap.INFO[wm.provider]["proxy"]:
            manager.ensure_map_port(inst)
        if wm.enabled:
            result = await webmap.prepare(inst, refresh=body.update or wm.provider != before.provider or not before.enabled)
        else:
            await webmap.disable(inst)
    except Exception:
        inst.cfg.webmap = before
        raise
    manager.save_server(inst.cfg)
    title = webmap.INFO[wm.provider]["title"]
    if wm.enabled and (not before.enabled or wm.provider != before.provider):
        inst.panel_log(f"Mapa {title} {(result or {}).get('version') or ''} gotowa – {squaremap.public_url(inst)}")
    elif before.enabled and not wm.enabled:
        inst.panel_log("Mapa wyłączona.")
    # Jars change only when the map is switched on/off or replaced; options are re-read on restart.
    changed = wm.model_dump(exclude={"port"}) != before.model_dump(exclude={"port"}) or bool((result or {}).get("updated"))
    state = await webmap.state(inst)
    return {**state, "restart_required": bool(changed and inst.running),
            "installed_files": (result or {}).get("installed_files", []), "updated": bool((result or {}).get("updated"))}


class WebMapRenderBody(BaseModel):
    action: Literal["start", "cancel"] = "start"
    mode: Literal["full", "update"] = "full"


@router.post("/servers/{sid}/webmap/render")
@run_errors
async def webmap_render(sid: str, body: WebMapRenderBody, _: str = Depends(auth.require_user)):
    inst = inst_or_404(sid)
    if body.action == "cancel":
        inst.map_render.cancel()
    else:
        inst.map_render.start(body.mode)
    await asyncio.sleep(0.3)  # let the map answer the first command
    return await webmap.state(inst)


# ====================================================================== LOD for Distant Horizons / Voxy

@router.get("/servers/{sid}/lod")
async def lod_get(sid: str, refresh: bool = False, _: str = Depends(auth.require_user)):
    return await lod.state(inst_or_404(sid), refresh=refresh)


class LodBody(BaseModel):
    distant_horizons: Optional[bool] = None
    voxy: Optional[bool] = None
    distance: Optional[int] = Field(default=None, ge=32, le=512)
    generate: Optional[bool] = None
    update: bool = False


@router.post("/servers/{sid}/lod")
@run_errors
async def lod_set(sid: str, body: LodBody, _: str = Depends(auth.require_user)):
    inst = inst_or_404(sid)
    before = inst.cfg.lod.model_copy()
    patch = body.model_dump(exclude_unset=True, exclude={"update"})
    for key in ("distant_horizons", "voxy"):
        if patch.get(key) and not lod.supported(inst, key):
            raise HTTPException(400, lod.unsupported_reason(inst, key) or "Ten silnik nie jest obsługiwany")
    inst.cfg.lod = before.model_copy(update=patch)
    try:
        results = await lod.prepare(inst, refresh=body.update)
    except Exception:
        inst.cfg.lod = before
        raise
    manager.save_server(inst.cfg)
    for key, r in results.items():
        title = lod.jars(inst)[key].title
        if not getattr(before, key):
            inst.panel_log(f"{title} {r.get('version') or ''} włączony – gracze z tym modem zobaczą daleki teren.")
    for key in ("distant_horizons", "voxy"):
        if getattr(before, key) and not getattr(inst.cfg.lod, key):
            inst.panel_log(f"{lod.jars(inst)[key].title} wyłączony.")
    changed = inst.cfg.lod != before or any(r.get("updated") for r in results.values())
    files = [f for r in results.values() for f in r.get("installed_files", [])]
    return {**await lod.state(inst), "restart_required": bool(changed and inst.running), "installed_files": files}


# ====================================================================== Chunky (world pre-generation)

@router.get("/servers/{sid}/chunky")
async def chunky_get(sid: str, refresh: bool = False, _: str = Depends(auth.require_user)):
    return await chunky.state(inst_or_404(sid), refresh=refresh)


@router.post("/servers/{sid}/chunky/install")
@run_errors
async def chunky_install(sid: str, _: str = Depends(auth.require_user)):
    inst = inst_or_404(sid)
    was = chunky.installed_info(inst)
    result = await chunky.install(inst)
    inst.panel_log(f"Chunky {result['version']} {'zaktualizowany' if was else 'zainstalowany'}.")
    return {**await chunky.state(inst), "restart_required": inst.running, "installed_files": result["installed_files"]}


@router.delete("/servers/{sid}/chunky")
@run_errors
async def chunky_remove(sid: str, _: str = Depends(auth.require_user)):
    inst = inst_or_404(sid)
    if inst.chunky_runner.active:
        inst.chunky_runner.cancel()
    await asyncio.to_thread(chunky.remove, inst)
    inst.panel_log("Chunky odinstalowany.")
    return {**await chunky.state(inst), "restart_required": inst.running}


@router.put("/servers/{sid}/chunky/settings")
@run_errors
async def chunky_settings(sid: str, body: ChunkySettings, _: str = Depends(auth.require_user)):
    inst = inst_or_404(sid)
    multicore_changed = body.multicore != inst.cfg.chunky.multicore
    previous = inst.cfg.chunky
    inst.cfg.chunky = body
    if multicore_changed:
        try:
            await chunky.set_multicore(inst, body.multicore)
        except Exception:
            inst.cfg.chunky = previous
            raise
    manager.save_server(inst.cfg)
    return {**await chunky.state(inst), "restart_required": bool(multicore_changed and inst.running)}


class ChunkyAction(BaseModel):
    action: Literal["start", "pause", "continue", "cancel"]


@router.post("/servers/{sid}/chunky/action")
@run_errors
async def chunky_action(sid: str, body: ChunkyAction, _: str = Depends(auth.require_user)):
    inst = inst_or_404(sid)
    if not chunky.installed_info(inst):
        raise HTTPException(400, "Najpierw zainstaluj Chunky")
    runner = inst.chunky_runner
    if body.action == "start":
        if inst.state != "online":
            raise HTTPException(400, "Uruchom serwer, aby zacząć generowanie świata")
        runner.start()
    elif body.action == "pause":
        runner.pause()
    elif body.action == "continue":
        runner.resume()
    else:
        runner.cancel()
    await asyncio.sleep(0.3)  # let Chunky answer the first commands
    return await chunky.state(inst)


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
    compat = bedrock_compat.quick_state(inst, stats.get("java_version"))
    advertise = stats.get("advertise", [])
    # Geyser has one list of game-connection addresses for every server. When it holds this server's own
    # player address the two match; otherwise both must at least lead to the same IP.
    from .bedrock import normalize_host, resolve_ips
    advertise_own = bool(host) and host in [normalize_host(a) for a in advertise]
    address_ips: set[str] = set(resolve_ips(host)) if host and not advertise_own else set()
    advertise_ips: set[str] = set()
    if address_ips:
        for a in advertise:
            advertise_ips |= resolve_ips(normalize_host(a))
    advertise_match = None if not (address_ips and advertise_ips) else bool(address_ips & advertise_ips)
    return {
        "enabled": inst.cfg.bedrock_enabled,
        "supported": compat.get("supported", False),
        "compat": compat,
        "public_host": host,
        "public_port": BEDROCK_PORT,
        "address": address,
        "hostnames": hosts,
        "gateway_running": bool(bp and bp.running),
        "gateway_listening": bool(bp and bp.listening),
        "gateway_error": bp.error if bp else None,
        "gateway_log": stats.get("log", []),
        "gateway_version": stats.get("version"),
        "router_version": stats.get("router_version"),
        "conflicts": stats.get("conflicts", []),
        "advertise": advertise,
        "advertise_source": stats.get("advertise_source"),
        "advertise_own": advertise_own,
        "advertise_match": advertise_match,
        "address_ips": sorted(address_ips),
        "advertise_ips": sorted(advertise_ips),
        "address_resolves": bool(host) and bool(resolve_ips(host)),
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
    if body.enabled:
        bp = manager.bedrock
        geyser_java = bp.geyser_java_version if bp else None
        compat = bedrock_compat.quick_state(inst, geyser_java)
        if not compat.get("supported"):
            raise ValueError(compat.get("reason", "Serwer nie jest zgodny z Geyserem."))
        # Installation happens before persistence, so a failed download never
        # leaves a server enabled with a route that cannot work.
        await bedrock_compat.ensure(inst, geyser_java)
    inst.cfg.bedrock_enabled = body.enabled
    manager.save_server(inst.cfg)
    if manager.bedrock:
        manager.bedrock.request_apply()
    inst.broadcast({"type": "state", "state": inst.state, "summary": inst.summary()})
    return _bedrock_state(inst)


def _bedrock_log_state(after: int = 0) -> dict:
    bp = manager.bedrock
    if bp is None:
        return {"entries": [], "next": 0, "epoch": 0, "enabled": False, "running": False,
                "listening": False, "error": None, "version": "", "host": "", "port": BEDROCK_PORT,
                "channel": manager.settings.bedrock_geyser_channel, "preview": manager.settings.bedrock_geyser_preview,
                "installed_preview": False, "integrated_pack": manager.settings.bedrock_integrated_pack}
    return bp.logs(max(0, after))


@router.get("/bedrock/logs")
async def bedrock_logs(after: int = 0, _: str = Depends(auth.require_user)):
    return _bedrock_log_state(after)


class GeyserChannelBody(BaseModel):
    channel: Optional[Literal["stable", "preview"]] = None
    preview: Optional[str] = Field(default=None, max_length=20)
    integrated_pack: Optional[bool] = None


@router.put("/bedrock/geyser")
async def bedrock_geyser_channel(body: GeyserChannelBody, _: str = Depends(auth.require_user)):
    """Switch the Bedrock gateway between the stable Geyser release and a pull-request test build."""
    s = manager.settings
    if body.preview is not None:
        preview = body.preview.strip().lower()
        if preview.isdigit():
            preview = f"pr.{preview}"
        if not re.match(r"^pr\.[0-9]{1,6}$", preview):
            raise HTTPException(400, "Podaj numer PR Geysera, np. 6873")
        s.bedrock_geyser_preview = preview
    build_changed = body.channel is not None or body.preview is not None
    if body.channel is not None:
        s.bedrock_geyser_channel = body.channel
    if body.integrated_pack is not None:
        s.bedrock_integrated_pack = body.integrated_pack
    manager.save_settings(s)
    if manager.bedrock is not None:
        # A different build swaps the JAR; any config.yml change restarts Geyser on the next sync.
        manager.bedrock.recheck_geyser() if build_changed else manager.bedrock.request_apply()
    return _bedrock_log_state()


class GeyserCommandBody(BaseModel):
    command: str = Field(min_length=1, max_length=200)


@router.post("/bedrock/command")
async def bedrock_command(body: GeyserCommandBody, _: str = Depends(auth.require_user)):
    command = body.command.strip()
    if not command or any(ord(c) < 32 for c in command):
        raise HTTPException(400, "Niepoprawna komenda")
    if manager.bedrock is None:
        raise HTTPException(400, "Bramka Bedrock nie jest dostępna")
    try:
        await manager.bedrock.send_command(command)
    except (RuntimeError, OSError) as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True}


@router.delete("/bedrock/logs")
async def bedrock_logs_clear(_: str = Depends(auth.require_user)):
    bp = manager.bedrock
    if bp is not None:
        bp.clear_logs()
    return _bedrock_log_state()
