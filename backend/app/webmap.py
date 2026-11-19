"""Live web map of a server: squaremap, Dynmap or BlueMap, chosen in Add-ons → Services.

* squaremap (2D) writes a static web directory that the panel serves itself.
* Dynmap (flat 2D, optionally isometric "3D" views) and BlueMap (true 3D, optionally flat only) run
  their own small web servers. The panel binds them to 127.0.0.1 on a private port and proxies
  ``/map/<server id>/`` to them, so nothing extra is exposed.

All three render changed and newly generated chunks by themselves; nobody has to run a reload
command. Terrain that existed before the map was installed is drawn by a full render, which the
panel starts only when the owner presses "Render map" (Dynmap and squaremap); BlueMap scans the
world on its own when it starts. The panel never starts full renders automatically.

Settings owned by the panel: player markers on/off, 2D/3D, loopback web server, low render thread
counts, web chat off (a public page must not let visitors write into the game chat).
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from . import content, squaremap
from .managedjar import ManagedJar, github_release_asset, mc_tuple
from .modpacks import ModpackError
from .store import atomic_write_text

if TYPE_CHECKING:
    from .manager import ServerInstance

log = logging.getLogger("minenode.webmap")

PROVIDER_IDS = ("squaremap", "dynmap", "bluemap")
INFO = {
    "squaremap": {"title": "squaremap", "three_d": False, "proxy": False, "incremental": False,
                  "engines": ("paper", "purpur", "fabric", "quilt", "forge", "neoforge"),
                  "about": "Lekka mapa 2D z góry. Najmniejsze obciążenie serwera."},
    "dynmap": {"title": "Dynmap", "three_d": True, "proxy": True, "incremental": False,
               "engines": ("paper", "purpur", "fabric", "quilt", "forge"),
               "about": "Klasyczna mapa w stylu Google Maps: widok płaski, opcjonalnie izometryczny."},
    "bluemap": {"title": "BlueMap", "three_d": True, "proxy": True, "incremental": True,
                "engines": ("paper", "purpur", "fabric", "quilt", "forge", "neoforge"),
                "about": "Mapa 3D, po której można latać. Opcjonalnie tylko lekki widok płaski."},
}

# ====================================================================== Dynmap

DYNMAP_ID = "fRQREgAc"
_DYNMAP_SPIGOT_RANGE = ((1, 13, 2), (26, 3))  # bukkit helpers bundled in the CE spigot jar
_DYNMAP_ASSET = re.compile(r"^Dynmap-CE-[\w.]+-(fabric|forge)-(\d+(?:\.\d+)+)(?:-(\d+))?\.jar$", re.I)


def _dynmap_asset_fits(name: str, loader: str, mc: str) -> bool:
    m = _DYNMAP_ASSET.match(name)
    if not m or m.group(1).lower() != loader:
        return False
    spec, upper = m.group(2), m.group(3)
    want = mc_tuple(mc)
    low = mc_tuple(spec)
    if upper:  # "1.21.9-10" covers 1.21.9 … 1.21.10
        return low[:2] == want[:2] and low[2:3] <= want[2:3] <= (int(upper),)
    if len(low) == 2:  # "26.1" covers every 26.1.x patch release
        return want[:2] == low
    return want == low


async def _dynmap_github(inst: "ServerInstance") -> Optional[dict]:
    """Dynmap Community Edition builds from webbukkit/dynmap releases.

    Paper/Purpur use the single spigot jar (Modrinth has Paper builds only up to 1.21.4). Fabric and
    Forge use it only when Modrinth has no build for this exact version (26.x)."""
    engine, mc = inst.cfg.type, inst.cfg.mc_version
    if engine in ("paper", "purpur"):
        low, high = _DYNMAP_SPIGOT_RANGE
        v = mc_tuple(mc)
        if v < low:
            return None
        got = await github_release_asset("webbukkit/dynmap", "ce-", lambda n: n.lower().endswith("-spigot.jar"))
        if got:
            got["compat"] = "ok" if v[:2] <= high else "warn"
        return got
    loader = "fabric" if engine in ("fabric", "quilt") else "forge" if engine == "forge" else None
    if not loader:
        return None
    return await github_release_asset("webbukkit/dynmap", "ce-", lambda n: _dynmap_asset_fits(n, loader, mc))


DYNMAP = ManagedJar("dynmap", "Dynmap", DYNMAP_ID, r"^Dynmap(?:-CE)?-v?\d[\w.\-]*\.jar$",
                    {"paper": ["paper", "spigot"], "purpur": ["paper", "spigot"], "fabric": ["fabric"],
                     "quilt": ["fabric"], "forge": ["forge"]},
                    fallback="never", extra=_dynmap_github,
                    extra_first=lambda inst: inst.cfg.type in ("paper", "purpur"))


def dynmap_dir(inst: "ServerInstance") -> Path:
    return inst.dir / "plugins" / "dynmap" if inst.cfg.type in ("paper", "purpur") else inst.dir / "dynmap"


def _dynmap_worlds(inst: "ServerInstance") -> list[str]:
    level = (inst.props().get("level-name") or "world").strip() or "world"
    if inst.cfg.type in ("paper", "purpur"):
        return [level, f"{level}_nether", f"{level}_the_end"]
    return [level, "DIM-1", "DIM1"]  # names Dynmap gives the vanilla dimensions on Fabric/Forge


def _dynmap_templates(three_d: bool) -> str:
    """Overrides of Dynmap's "lowres" templates: flat map only (2D) or flat + isometric surface (3D).

    The cave view of the stock template is left out – it doubles render work for little benefit."""
    def env(name: str, surface_name: str, prefix: str, lighting: str, background: str = "") -> str:
        bg = f'        background: "{background}"\n' if background else ""
        out = (f"  {name}-lowres:\n    enabled: true\n    extrazoomout: 2\n    maps:\n"
               "      - class: org.dynmap.hdmap.HDMap\n        name: flat\n        title: \"Flat\"\n"
               "        prefix: flat\n        perspective: iso_S_90_lowres\n        shader: stdtexture\n"
               f"        lighting: {lighting}\n{bg}        mapzoomin: 2\n")
        if three_d:
            out += ("      - class: org.dynmap.hdmap.HDMap\n"
                    f"        name: {surface_name}\n        title: \"Surface\"\n        prefix: {prefix}\n"
                    "        perspective: iso_SE_60_lowres\n        shader: stdtexture\n"
                    f"        lighting: {lighting}\n{bg}        mapzoomin: 2\n")
        return out
    return ("# Written by MineNode (Add-ons -> Services -> map). Changes here are overwritten.\n"
            "version: 0.20\ntemplates:\n"
            + env("normal", "surface", "t", "shadows")
            + env("nether", "nether", "nt", "nethershadows", "#300806")
            + env("the_end", "the_end", "st", "brightnight"))


def _set_nested(text: str, key: str, value: str) -> str:
    """Set every occurrence of an indented ``key:`` (Dynmap component options such as allowwebchat)."""
    return re.sub(rf"^(\s+){re.escape(key)}\s*:.*$", rf"\g<1>{key}: {value}", text, flags=re.M)


def dynmap_write_config(inst: "ServerInstance") -> None:
    d = dynmap_dir(inst)
    d.mkdir(parents=True, exist_ok=True)
    path = d / "configuration.txt"
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        text = _from_jar(DYNMAP, inst, "configuration.txt") or ""
    wm = inst.cfg.webmap
    owned = (
        ("disable-webserver", "false"),
        ("webserver-bindaddress", "127.0.0.1"),
        ("webserver-port", str(wm.port or 8123)),
        ("deftemplatesuffix", "lowres"),
        ("display-whitelist", "false" if wm.show_players else "true"),
        ("tiles-rendered-at-once", "1"),
        ("login-enabled", "false"),
    )
    for key, value in owned:
        text = squaremap._yaml_set(text, (key,), value)
    text = _set_nested(text, "allowwebchat", "false")
    text = _set_nested(text, "sendposition", "true" if wm.show_players else "false")
    atomic_write_text(path, text)
    tdir = d / "templates"
    tdir.mkdir(parents=True, exist_ok=True)
    atomic_write_text(tdir / "custom-minenode.txt", _dynmap_templates(wm.render_3d))


# ====================================================================== BlueMap

BLUEMAP_ID = "swbUV1cr"
BLUEMAP = ManagedJar("bluemap", "BlueMap", BLUEMAP_ID, r"^bluemap-\d[\w.\-]*\.jar$",
                     {"paper": ["paper"], "purpur": ["paper"], "fabric": ["fabric"], "quilt": ["fabric"],
                      "forge": ["forge"], "neoforge": ["neoforge"]}, fallback="plugin")
BLUEMAP_WEBROOT = "bluemap/web"


def bluemap_dir(inst: "ServerInstance") -> Path:
    return inst.dir / "plugins" / "BlueMap" if inst.cfg.type in ("paper", "purpur") else inst.dir / "config" / "bluemap"


_HOCON_KEY = r"^{key}\s*[:=].*$"


def _hocon_set(text: str, key: str, value: str) -> str:
    """Set a top-level HOCON key (BlueMap writes one ``key: value`` per line)."""
    pattern = re.compile(_HOCON_KEY.format(key=re.escape(key)), re.M)
    line = f"{key}: {value}"
    if pattern.search(text):
        return pattern.sub(line.replace("\\", "\\\\"), text, count=1)
    return text.rstrip("\n") + ("\n" if text.strip() else "") + line + "\n"


def _edit(path: Path, values: dict[str, str], header: str = "") -> bool:
    try:
        old = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        old = None
    text = old if old is not None else header
    for k, v in values.items():
        text = _hocon_set(text, k, v)
    if text == old:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, text)
    return True


def bluemap_write_config(inst: "ServerInstance", accept: Optional[bool] = None) -> bool:
    """Write BlueMap's configs; returns True when anything changed.

    The first start runs with ``accept-download: false``: BlueMap then only generates its configs
    (one file per world in maps/) without rendering. The panel adjusts the map files to the chosen
    2D/3D mode and accepts the download once the server is online (``accept=True``), so the first
    render already uses the right mode. Enabling BlueMap in the panel is the owner's agreement to
    BlueMap downloading the Minecraft client resources from Mojang (Mojang EULA, shown in the panel)."""
    d = bluemap_dir(inst)
    wm = inst.cfg.webmap
    maps = sorted((d / "maps").glob("*.conf")) if (d / "maps").is_dir() else []
    if accept is None:
        accept = bool(maps) or _accepted(d)
    changed = _edit(d / "core.conf", {
        "accept-download": "true" if accept else "false",
        "render-thread-count": "1",
    }, header='# MineNode: accept-download is set by the panel when BlueMap is enabled.\ndata: "bluemap"\nmetrics: false\n')
    changed |= _edit(d / "webserver.conf", {
        "enabled": "true", "webroot": f'"{BLUEMAP_WEBROOT}"', "ip": '"127.0.0.1"', "port": str(wm.port or 8100),
    })
    changed |= _edit(d / "webapp.conf", {
        "enabled": "true", "webroot": f'"{BLUEMAP_WEBROOT}"',
        "default-to-flat-view": "false" if wm.render_3d else "true",
    })
    changed |= _edit(d / "plugin.conf", {"live-player-markers": "true" if wm.show_players else "false"})
    three = "true" if wm.render_3d else "false"
    for m in maps:
        changed |= _edit(m, {"enable-hires": three, "enable-perspective-view": three,
                             "enable-free-flight-view": three, "enable-flat-view": "true"})
    return changed


def _accepted(d: Path) -> bool:
    try:
        return bool(re.search(r"^accept-download\s*[:=]\s*true", (d / "core.conf").read_text(encoding="utf-8"), re.M))
    except OSError:
        return False


# ====================================================================== shared helpers

def _from_jar(jar: ManagedJar, inst: "ServerInstance", name: str) -> Optional[str]:
    """A default config file bundled in the installed jar (used before the first start)."""
    info = jar.installed_info(inst)
    if not info:
        return None
    path = content.content_dir(inst) / info["file"]
    if not path.is_file():
        path = path.with_name(path.name + ".disabled")
    try:
        with zipfile.ZipFile(path) as zf:
            return zf.read(name).decode("utf-8", "replace")
    except (OSError, KeyError, zipfile.BadZipFile):
        return None


def provider(inst: "ServerInstance") -> str:
    return inst.cfg.webmap.provider


def jar_for(pid: str) -> Optional[ManagedJar]:
    return {"dynmap": DYNMAP, "bluemap": BLUEMAP}.get(pid)


def engine_supported(inst: "ServerInstance", pid: str) -> bool:
    if inst.cfg.type not in INFO[pid]["engines"]:
        return False
    if pid == "squaremap" and inst.cfg.type == "forge":
        return mc_tuple(inst.cfg.mc_version) <= (1, 20, 1)  # squaremap dropped Forge after 1.20.1
    return True


def unsupported_reason(inst: "ServerInstance", pid: str) -> Optional[str]:
    if engine_supported(inst, pid):
        return None
    if inst.cfg.type == "vanilla":
        return "Serwer Vanilla nie obsługuje pluginów ani modów."
    if inst.cfg.type == "custom":
        return "Mapy instaluje się automatycznie tylko na znanych silnikach."
    if pid == "dynmap" and inst.cfg.type == "neoforge":
        return "Dynmap nie ma wersji dla NeoForge."
    if pid == "squaremap" and inst.cfg.type == "forge":
        return "squaremap dla Forge kończy się na Minecraft 1.20.1 – wybierz NeoForge lub inną mapę."
    return f"{INFO[pid]['title']} nie działa na tym silniku."


def data_dir(inst: "ServerInstance", pid: Optional[str] = None) -> Path:
    pid = pid or provider(inst)
    return {"squaremap": squaremap.data_dir, "dynmap": dynmap_dir, "bluemap": bluemap_dir}[pid](inst)


def web_dir(inst: "ServerInstance") -> Path:
    pid = provider(inst)
    if pid == "squaremap":
        return squaremap.web_dir(inst)
    if pid == "dynmap":
        return dynmap_dir(inst) / "web"
    return inst.dir / BLUEMAP_WEBROOT


def map_url(inst: "ServerInstance") -> str:
    return f"/map/{inst.cfg.id}/"


def map_available(inst: "ServerInstance") -> bool:
    """The map can be shown on the public page right now."""
    wm = inst.cfg.webmap
    if not wm.enabled or not engine_supported(inst, wm.provider):
        return False
    if not (web_dir(inst) / "index.html").is_file():
        return False
    return not INFO[wm.provider]["proxy"] or bool(inst.running)


def proxy_port(inst: "ServerInstance") -> Optional[int]:
    wm = inst.cfg.webmap
    if wm.enabled and INFO[wm.provider]["proxy"] and wm.port:
        return wm.port
    return None


# ====================================================================== install / start

def write_config(inst: "ServerInstance") -> None:
    pid = provider(inst)
    if pid == "squaremap":
        squaremap.write_config(inst)
    elif pid == "dynmap":
        dynmap_write_config(inst)
    else:
        bluemap_write_config(inst)


async def _ensure_provider(inst: "ServerInstance", pid: str, refresh: bool) -> dict:
    if pid == "squaremap":
        return await squaremap.ensure(inst, refresh=refresh) or {}
    return await jar_for(pid).ensure(inst, refresh=refresh)


async def _disable_others(inst: "ServerInstance", keep: Optional[str]) -> None:
    """Managed jars of maps that are not selected stay on disk but are not loaded."""
    for pid in PROVIDER_IDS:
        if pid == keep:
            continue
        if pid == "squaremap":
            info = squaremap.installed_info(inst)
            if info and info.get("managed"):
                await asyncio.to_thread(squaremap.disable_jar, inst)
            if info:  # a manual install must not bind the panel's port 8080
                await asyncio.to_thread(squaremap.write_config, inst)
        else:
            await asyncio.to_thread(jar_for(pid).disable, inst)


async def prepare(inst: "ServerInstance", refresh: bool = False) -> Optional[dict]:
    """Before a start (and when the map is switched on): install/update the chosen map, write its
    settings and make sure the other maps managed by the panel are not loaded."""
    wm = inst.cfg.webmap
    if not wm.enabled or not engine_supported(inst, wm.provider):
        await _disable_others(inst, None)
        return None
    await _disable_others(inst, wm.provider)
    result = await _ensure_provider(inst, wm.provider, refresh)
    await asyncio.to_thread(write_config, inst)
    return result


async def disable(inst: "ServerInstance") -> None:
    await _disable_others(inst, None)


async def on_online(inst: "ServerInstance") -> None:
    """BlueMap: adjust the configs it generated on the first start and accept the download."""
    if not inst.cfg.webmap.enabled or provider(inst) != "bluemap":
        return
    for attempt in range(2):
        await asyncio.sleep(8 if attempt == 0 else 30)
        if not inst.running or provider(inst) != "bluemap":
            return
        if await asyncio.to_thread(bluemap_write_config, inst, True):
            inst.panel_log("BlueMap: ustawienia mapy zapisane – przeładowuję BlueMap.")
            await inst.send_command("bluemap reload", echo=False)


async def state(inst: "ServerInstance", refresh: bool = False) -> dict:
    wm = inst.cfg.webmap
    providers = []
    for pid in PROVIDER_IDS:
        supported = engine_supported(inst, pid)
        entry = {"id": pid, **{k: v for k, v in INFO[pid].items() if k != "engines"},
                 "supported": supported, "reason": unsupported_reason(inst, pid)}
        if supported and (pid == wm.provider or refresh):
            entry.update(await _status(inst, pid, refresh))
        providers.append(entry)
    current = next(p for p in providers if p["id"] == wm.provider)
    return {
        "enabled": wm.enabled, "provider": wm.provider, "show_players": wm.show_players, "render_3d": wm.render_3d,
        "providers": providers, "current": current, "engine": inst.cfg.type, "mc_version": inst.cfg.mc_version,
        "map_available": map_available(inst), "map_url": map_url(inst), "public_url": squaremap.public_url(inst),
        "data_dir": squaremap.rel_to_server(inst, data_dir(inst)), "running": bool(inst.running),
        "online": inst.state == "online", "render": inst.map_render.status() if hasattr(inst, "map_render") else None,
        "restart_required": False,
    }


async def _status(inst: "ServerInstance", pid: str, refresh: bool) -> dict:
    if pid == "squaremap":
        s = await squaremap.state(inst, refresh=refresh)
        return {k: s.get(k) for k in ("installed", "version", "latest_version", "update_available", "available",
                                      "compat", "error")}
    return await jar_for(pid).status(inst, refresh=refresh)


# ====================================================================== manual full render

START_TIMEOUT = 45  # a world that does not confirm the render in time is skipped
BLUEMAP_FIRST_POLL = 3.0  # let BlueMap queue the update command before asking for its status
BLUEMAP_POLL = 8.0

_LOG = {
    "squaremap": (
        (re.compile(r"Started full map render for (\S+)"), "started"),
        (re.compile(r"A render is already in progress on (\S+)"), "started"),
        (re.compile(r"Finished rendering map for (\S+)"), "finished"),
        (re.compile(r"Rendering map for (\S+) has been interrupted"), "finished"),
        (re.compile(r"Render cancelled for (\S+)"), "finished"),
    ),
    "dynmap": (  # DynmapCore MapManager messages
        (re.compile(r"Full render (?:starting|resuming) on world '([^']+)'"), "started"),
        (re.compile(r"Full render of world '([^']+)' already active"), "started"),
        (re.compile(r"Full render of '([^']+)' finished"), "finished"),
    ),
}

# squaremap logs "(37.50%) World: minecraft:overworld Chunks: 120/320 ... Rate: 4.2 cps"
_SQUAREMAP_PROGRESS = re.compile(
    r"\((?P<percent>[\d.,]+)%\)\s+World:\s+(?P<world>\S+)\s+"
    r"(?:Regions:\s+[\d,]+/[\d,]+\s+)?"
    r"Chunks:\s+(?P<done>[\d,]+)/(?P<total>[\d,]+)\s+"
    r"Elapsed:\s+(?P<elapsed>\S+)\s+ETA:\s+(?P<eta>\S+)\s+Rate:\s+(?P<rate>[\d.,]+)\s+cps"
)
# Dynmap never reports a total, only the number of tiles rendered so far.
_DYNMAP_PROGRESS = re.compile(
    r"Full render of (?:map '[^']+'|maps \[[^\]]+\]) of '(?P<world>[^']+)' in progress - "
    r"(?P<count>[\d,]+) tiles rendered(?: each)? \((?P<msec>[\d.,]+) msec/(?:map-)?tile"
)
# "/bluemap" status output: "map <id> is currently being updated", "progress: 12.345%",
# "remaining time: 1 minute 20 seconds" and "N render-threads are idle" when the queue is empty.
_BLUEMAP_MAP = re.compile(r"\bmap\s+(?P<map>.+?)\s+is currently being (?:updated|purged)")
_BLUEMAP_PROGRESS = re.compile(r"\bprogress:\s*(?P<percent>[\d.,]+)\s*%")
_BLUEMAP_ETA = re.compile(r"\bremaining time:\s*(?P<eta>.+?)\s*$")
_BLUEMAP_IDLE = re.compile(r"\brender-threads?\s+(?:is|are)\s+idle\b")
_BLUEMAP_PAUSED = re.compile(r"\brender-threads?\s+(?:is|are)\s+paused\b")
_MC_CODE = re.compile(r"§.")


def _as_float(raw: str) -> float:
    raw = raw.strip().replace(" ", "").rstrip("%")
    if "," in raw and "." not in raw:
        raw = raw.replace(",", ".")
    else:
        raw = raw.replace(",", "")
    return float(raw)


def _as_int(raw: str) -> int:
    return int(re.sub(r"\D", "", raw) or 0)


def _bluemap_maps(inst: "ServerInstance") -> list[str]:
    """Ids of the maps BlueMap loaded from ``maps/*.conf`` (the command needs the id)."""
    try:
        return sorted(p.stem for p in (bluemap_dir(inst) / "maps").glob("*.conf"))
    except OSError:
        return []


class MapRender:
    """A render started from the panel: worlds one after another, never automatically."""

    def __init__(self, inst: "ServerInstance"):
        self.inst = inst
        self.queue: list[str] = []
        self.current: Optional[str] = None
        self.awaiting: Optional[tuple[str, float]] = None
        self.requested_at: Optional[float] = None
        self.finished_at: Optional[float] = None
        self.provider: Optional[str] = None
        self.mode = "full"  # "full" re-renders everything, "update" only changed tiles (BlueMap)
        self.total_worlds = 0
        self.finished_worlds = 0
        self.percent: Optional[float] = None
        self.world_percent: Optional[float] = None
        self.processed: Optional[int] = None
        self.processed_total: Optional[int] = None
        self.rate: Optional[float] = None
        self.eta: Optional[str] = None
        self.elapsed: Optional[str] = None
        self.unit: Optional[str] = None
        self.paused = False
        self.bluemap_active = False
        self._bluemap_next = 0.0
        self._bluemap_idle = 0
        self._bluemap_seen = False

    @property
    def running(self) -> bool:
        return bool(self.current or self.awaiting or self.queue or self.bluemap_active)

    def status(self) -> dict:
        def rounded(value: Optional[float]) -> Optional[float]:
            return round(value, 1) if value is not None else None

        return {
            "running": self.running, "current": self.current or (self.awaiting or (None,))[0],
            "queue": list(self.queue), "requested_at": self.requested_at, "finished_at": self.finished_at,
            "provider": self.provider, "mode": self.mode, "percent": rounded(self.percent),
            "world_percent": rounded(self.world_percent), "processed": self.processed,
            "total": self.processed_total, "rate": rounded(self.rate), "eta": self.eta,
            "elapsed": self.elapsed, "unit": self.unit, "worlds_done": self.finished_worlds,
            "worlds_total": self.total_worlds, "paused": self.paused,
        }

    def _worlds(self, pid: str) -> list[str]:
        if pid == "squaremap":
            return list(squaremap.WORLDS)
        return _dynmap_worlds(self.inst)

    def _send(self, cmd: str) -> None:
        self.inst.spawn(self.inst.send_command(cmd, echo=False))

    def _sync_percent(self) -> None:
        """Overall progress across the worlds; Dynmap reports no total, so it keeps ``None``."""
        if self.provider == "dynmap" or not self.total_worlds:
            return
        fraction = 0.0 if self.world_percent is None else max(0.0, min(100.0, self.world_percent)) / 100.0
        self.percent = min(100.0, (self.finished_worlds + fraction) / self.total_worlds * 100.0)

    def _reset_progress(self) -> None:
        self.percent = None if self.provider == "dynmap" else 0.0
        self.world_percent = None if self.provider == "dynmap" else 0.0
        self.processed = self.processed_total = None
        self.rate = self.eta = self.elapsed = None
        self.paused = False

    def start(self, mode: str = "full") -> dict:
        inst = self.inst
        pid = provider(inst)
        if mode not in ("full", "update"):
            raise ModpackError("Nieznany tryb renderowania mapy")
        if not inst.cfg.webmap.enabled:
            raise ModpackError("Najpierw włącz mapę")
        if inst.state != "online":
            raise ModpackError("Serwer musi być uruchomiony, żeby wygenerować mapę")
        if self.running:
            raise ModpackError("Mapa już się renderuje")
        if mode == "update" and not INFO[pid]["incremental"]:
            raise ModpackError(f"{INFO[pid]['title']} sam dorysowuje zmienione fragmenty – "
                               "dostępny jest tylko pełny render")
        maps = _bluemap_maps(inst) if pid == "bluemap" else []
        if pid == "bluemap" and not maps:
            raise ModpackError("BlueMap nie ma jeszcze gotowych map – uruchom serwer i spróbuj ponownie")
        self.provider, self.mode = pid, mode
        self.requested_at, self.finished_at = time.time(), None
        self.finished_worlds = 0
        self._reset_progress()
        if pid == "bluemap":
            # BlueMap renders every map in the background and reports progress in /bluemap.
            # From the console the command needs an explicit map id (the world-only variant works
            # just for players). "update" keeps tiles that did not change, "force-update" redoes all.
            self.total_worlds = len(maps)
            self.unit = None
            self.bluemap_active = True
            self._bluemap_idle = 0
            self._bluemap_seen = False
            self._bluemap_next = time.time() + BLUEMAP_FIRST_POLL
            command = "update" if mode == "update" else "force-update"
            for map_id in maps:
                self._send(f"bluemap {command} {map_id}")
            inst.panel_log("BlueMap: aktualizowanie zmienionych fragmentów map rozpoczęte." if mode == "update"
                           else "BlueMap: ponowne renderowanie wszystkich map rozpoczęte.")
            return self.status()
        self.queue = self._worlds(pid)
        self.total_worlds = len(self.queue)
        self.unit = "chunks" if pid == "squaremap" else "tiles"
        inst.panel_log(f"{INFO[pid]['title']}: renderowanie mapy rozpoczęte ({', '.join(self.queue)}).")
        self._next(time.time())
        return self.status()

    def cancel(self) -> None:
        pid = self.provider or provider(self.inst)
        inst = self.inst
        if pid == "bluemap":
            if self.bluemap_active and inst.state == "online":
                self._send("bluemap tasks cancel all")
            self.bluemap_active = False
            self.current = None
            self.eta = None
            inst.panel_log("Renderowanie mapy przerwane.")
            return
        world = self.current or (self.awaiting or (None,))[0]
        self.queue.clear()
        self.awaiting = None
        if world and inst.state == "online":
            label = squaremap.command_label(inst) if pid == "squaremap" else "dynmap"
            self._send(f"{label} cancelrender {world}")
        self.current = None
        inst.panel_log("Renderowanie mapy przerwane.")

    def _next(self, now: float) -> None:
        if not self.queue:
            self.current, self.awaiting = None, None
            self.finished_worlds = self.total_worlds
            self.world_percent = 100.0
            self.percent = 100.0
            self.rate = self.eta = None
            if self.requested_at and not self.finished_at:
                self.finished_at = now
                self.inst.panel_log("Mapa została wyrenderowana.")
            return
        world = self.queue.pop(0)
        pid = self.provider or provider(self.inst)
        label = squaremap.command_label(self.inst) if pid == "squaremap" else "dynmap"
        self.awaiting = (world, now)
        self.processed = self.processed_total = None
        self.rate = self.eta = self.elapsed = None
        self.world_percent = None if pid == "dynmap" else 0.0
        self._sync_percent()
        self._send(f"{label} fullrender {world}")

    def on_line(self, plain: str) -> None:
        pid = self.provider
        if not pid:
            return
        plain = _MC_CODE.sub("", plain)
        if pid == "bluemap":
            self._on_bluemap_line(plain)
            return
        if pid not in _LOG or not (self.current or self.awaiting):
            return
        if pid == "squaremap" and (m := _SQUAREMAP_PROGRESS.search(plain)):
            world = squaremap._world_id(m.group("world"))
            self.current, self.awaiting = world, None
            self.world_percent = _as_float(m.group("percent"))
            self.processed = _as_int(m.group("done"))
            self.processed_total = _as_int(m.group("total"))
            self.rate = _as_float(m.group("rate"))
            self.eta = m.group("eta")
            self.elapsed = m.group("elapsed")
            self._sync_percent()
            return
        if pid == "dynmap" and (m := _DYNMAP_PROGRESS.search(plain)):
            self.current, self.awaiting = m.group("world").strip(), None
            self.processed = _as_int(m.group("count"))
            try:
                self.rate = round(1000.0 / _as_float(m.group("msec")), 1)
            except (ValueError, ZeroDivisionError):
                self.rate = None
            return
        for pattern, kind in _LOG[pid]:
            m = pattern.search(plain)
            if not m:
                continue
            world = m.group(1).strip().rstrip(".!,")
            if pid == "squaremap":
                world = squaremap._world_id(world)
            if kind == "started":
                if self.current != world:
                    self.processed = self.processed_total = None
                    self.rate = self.eta = self.elapsed = None
                    self.world_percent = None if pid == "dynmap" else 0.0
                self.current, self.awaiting = world, None
                self._sync_percent()
            elif kind == "finished" and world == (self.current or (self.awaiting or (None,))[0]):
                self.current = None
                self.awaiting = None
                self.finished_worlds += 1
                if self.total_worlds:
                    self.finished_worlds = min(self.finished_worlds, self.total_worlds)
                self.world_percent = 100.0
                self._next(time.time())
            return

    def _on_bluemap_line(self, plain: str) -> None:
        if not self.bluemap_active:
            return
        if m := _BLUEMAP_PROGRESS.search(plain):
            try:
                self.world_percent = _as_float(m.group("percent"))
            except ValueError:
                return
            self._bluemap_idle = 0
            self._bluemap_seen = True
            self._sync_percent()
            # BlueMap prints "progress: … • remaining time: …" on one line.
            if e := _BLUEMAP_ETA.search(plain):
                self.eta = e.group("eta").strip()
            return
        if m := _BLUEMAP_MAP.search(plain):
            world = m.group("map").strip().strip("'\"")
            self._bluemap_idle = 0
            self._bluemap_seen = True
            if self.current and world != self.current:
                self.finished_worlds += 1
                if self.total_worlds:
                    self.finished_worlds = min(self.finished_worlds, self.total_worlds)
                self.world_percent = 0.0
            self.current = world
            self.paused = False
            return
        if m := _BLUEMAP_ETA.search(plain):
            self.eta = m.group("eta").strip()
            return
        if _BLUEMAP_IDLE.search(plain):
            # Right after the command one idle answer can still be a gap before the task starts;
            # a render that was actually running is finished on the first idle answer.
            if self._bluemap_seen:
                self._finish_bluemap()
            else:
                self._bluemap_idle += 1
                if self._bluemap_idle >= 2:
                    self._finish_bluemap()
            return
        if _BLUEMAP_PAUSED.search(plain):
            self.paused = True

    def _finish_bluemap(self, now: Optional[float] = None) -> None:
        if not self.bluemap_active:
            return
        self.bluemap_active = False
        self.current = None
        self.finished_worlds = self.total_worlds
        self.world_percent = 100.0
        self.percent = 100.0
        self.eta = None
        self.paused = False
        self._bluemap_idle = 0
        self._bluemap_seen = False
        if self.requested_at and not self.finished_at:
            self.finished_at = now or time.time()
            self.inst.panel_log("Mapa została zaktualizowana." if self.mode == "update"
                                else "Mapa została wyrenderowana.")

    def tick(self, now: float) -> None:
        if self.bluemap_active:
            if now >= self._bluemap_next:
                self._bluemap_next = now + BLUEMAP_POLL
                if self.inst.state == "online":
                    self._send("bluemap")
            return
        if self.awaiting and now - self.awaiting[1] >= START_TIMEOUT:
            self.awaiting = None  # unknown or disabled dimension
            self.finished_worlds += 1
            if self.total_worlds:
                self.finished_worlds = min(self.finished_worlds, self.total_worlds)
            self.world_percent = 100.0
            self._next(now)

    def on_stop(self) -> None:
        self.queue.clear()
        self.current = self.awaiting = None
        self.bluemap_active = False
        self._bluemap_idle = 0
        self._bluemap_seen = False
        self.eta = None
