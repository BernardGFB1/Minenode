"""Chunky integration: world pre-generation from the server's Add-ons tab.

Chunky (https://modrinth.com/plugin/chunky) is a plugin on Paper/Purpur and a mod on Fabric/Quilt/
Forge/NeoForge. MineNode installs the build for the server's engine and Minecraft version, keeps the
generation settings (world, shape, pattern, center, radius) and drives Chunky through console
commands. Progress is read from Chunky's own log lines (common/src/main/resources/lang/en.json).

Generation can be limited to the times nobody plays: the task is paused when a player joins and
continued once the server has been empty for a while. Chunky saves progress itself, so a paused or
interrupted task continues where it stopped (also after a restart).

"Multi-core generation" uses what the engine offers:
* Paper/Purpur generate chunks in parallel on ``chunk-system.worker-threads`` threads
  (config/paper-global.yml). By default Paper uses half of the physical cores; the option raises it
  to every available core but one.
* Fabric/Quilt need the C2ME mod (Concurrent Chunk Management Engine) for parallel generation.
* Forge/NeoForge have no reliable option, so it is not offered there.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING

from . import content
from .modpacks import ModpackError
from .store import atomic_write_text

if TYPE_CHECKING:
    from .manager import ServerInstance

log = logging.getLogger("minenode.chunky")

PROJECT_ID = "fALzjamp"   # Chunky on Modrinth
C2ME_ID = "VSNURh3q"      # C2ME (Fabric) on Modrinth

# Chunky publishes one build per platform; Quilt runs the Fabric build and Purpur the Paper one.
LOADERS: dict[str, list[str]] = {
    "paper": ["paper"], "purpur": ["paper"],
    "fabric": ["fabric"], "quilt": ["fabric"],
    "forge": ["forge"], "neoforge": ["neoforge"],
}
_JAR = re.compile(r"^Chunky-(?:Bukkit|Paper|Folia|Fabric|Forge|NeoForge|Sponge)-.*\.jar$", re.I)
_C2ME_JAR = re.compile(r"^c2me-fabric.*\.jar$", re.I)

WORLDS = ("overworld", "the_nether", "the_end")
SHAPES = ("square", "circle", "rectangle", "ellipse", "diamond", "pentagon", "hexagon", "star", "triangle")
PATTERNS = ("concentric", "loop", "spiral")

IDLE_DELAY = 60          # seconds without players before an "only when empty" task continues
_VERSION_TTL = 300.0
_version_cache: dict[tuple[str, str, str], tuple[float, dict | None]] = {}

# Chunky's console messages. The world is a level name (Paper) or a dimension id (mods).
_W = r"(\S+?)"
_LOG = (
    (re.compile(rf"Task started in {_W} for the "), "started"),
    (re.compile(rf"Task running for {_W}\. Processed: ([\d,]+) chunks \(([\d.,]+)%\), ETA: ([\d:]+), Rate: ([\d.,]+) cps"), "progress"),
    (re.compile(rf"Task finished for {_W}\. Processed: ([\d,]+) chunks \(([\d.,]+)%\), Total time: ([\d:]+)"), "finished"),
    (re.compile(rf"Task paused for {_W}\."), "paused"),
    (re.compile(rf"Task continuing for {_W}\."), "continuing"),
    (re.compile(rf"Task cancelled for {_W}\."), "cancelled"),
    (re.compile(rf"Task stopped for {_W}\."), "stopped"),
    (re.compile(r"No tasks to continue\."), "none"),
    (re.compile(rf"Task already started for {_W}!"), "started"),
)


def engine_supported(inst: "ServerInstance") -> bool:
    return inst.cfg.type in LOADERS


def unsupported_reason(inst: "ServerInstance") -> str | None:
    if engine_supported(inst):
        return None
    if inst.cfg.type == "vanilla":
        return "Serwer Vanilla nie obsługuje pluginów ani modów – Chunky wymaga Paper, Purpur, Fabric, Quilt, Forge lub NeoForge."
    return "Ten silnik nie obsługuje pluginów ani modów."


def kind(inst: "ServerInstance") -> str | None:
    return content.kind_for(inst.cfg.type)


def world_arg(inst: "ServerInstance", world: str) -> str:
    """The name Chunky expects: Bukkit world names on Paper, dimension ids on mod loaders."""
    if inst.cfg.type in ("paper", "purpur"):
        level = (inst.props().get("level-name") or "world").strip() or "world"
        return {"overworld": level, "the_nether": f"{level}_nether", "the_end": f"{level}_the_end"}[world]
    return f"minecraft:{world}"


# ====================================================================== installed files

def _files(inst: "ServerInstance", pattern: re.Pattern) -> list[Path]:
    d = content.content_dir(inst)
    if not d.is_dir():
        return []
    return sorted((p for p in d.iterdir() if pattern.match(p.name.removesuffix(".disabled"))), key=lambda p: p.name)


def installed_info(inst: "ServerInstance") -> dict | None:
    jars = [p for p in _files(inst, _JAR) if p.suffix == ".jar"]
    if not jars:
        return None
    name = jars[-1].name
    meta = content.load_meta(inst).get("files", {}).get(name, {})
    m = re.search(r"-(\d+(?:\.\d+)+)\.jar$", name)
    return {"file": name, "version_id": meta.get("version_id"),
            "version_number": meta.get("version_number") or (m.group(1) if m else None)}


def c2me_installed(inst: "ServerInstance") -> bool:
    return any(p.suffix == ".jar" for p in _files(inst, _C2ME_JAR))


async def _latest(project: str, inst: "ServerInstance", loaders: list[str], refresh: bool = False) -> dict | None:
    key = (project, inst.cfg.type, inst.cfg.mc_version)
    hit = _version_cache.get(key)
    if hit and not refresh and time.time() - hit[0] < _VERSION_TTL:
        return hit[1]
    try:
        versions = await content.modrinth_versions(project, inst.cfg.mc_version, loaders)
    except Exception:
        if hit:
            return hit[1]
        raise
    releases = [v for v in versions if v.get("version_type") == "release"]
    version = (releases or versions or [None])[0]
    _version_cache[key] = (time.time(), version)
    return version


async def latest_version(inst: "ServerInstance", refresh: bool = False) -> dict | None:
    if not engine_supported(inst):
        return None
    return await _latest(PROJECT_ID, inst, LOADERS[inst.cfg.type], refresh)


async def install(inst: "ServerInstance") -> dict:
    """Install or update Chunky (plus Fabric API on Fabric/Quilt) for this engine and version."""
    if not engine_supported(inst):
        raise ModpackError(unsupported_reason(inst) or "Ten silnik nie obsługuje Chunky")
    version = await latest_version(inst, refresh=True)
    if not version:
        raise ModpackError(f"Chunky nie ma wersji dla {inst.cfg.type} {inst.cfg.mc_version}")
    installer = content.Installer(inst, "")
    await installer.modrinth(PROJECT_ID, version_id=version["id"])
    keep = (installed_info(inst) or {}).get("file")
    for p in _files(inst, _JAR):  # older builds and other platforms' builds
        if p.name != keep:
            p.unlink(missing_ok=True)
    await asyncio.to_thread(prepare, inst)
    return {"version": version.get("version_number"), "installed_files": installer.installed}


def remove(inst: "ServerInstance") -> None:
    for p in _files(inst, _JAR):
        content.remove(inst, p.name.removesuffix(".disabled"))


# ====================================================================== server-side settings

def prepare(inst: "ServerInstance") -> None:
    """Settings Chunky needs from the server, applied before a start.

    Minecraft 1.21.2+ pauses an empty server after ``pause-when-empty-seconds`` – generation while
    nobody plays would stop (Chunky itself rewrites the key and asks for a restart). MineNode turns
    it off up front while Chunky is installed.
    """
    from .properties import read_properties, update_properties
    path = inst.dir / "server.properties"
    if path.exists() and read_properties(path).get("pause-when-empty-seconds", "0") not in ("0", "-1"):
        update_properties(path, {"pause-when-empty-seconds": 0})
    if inst.cfg.type in ("paper", "purpur"):
        apply_paper_threads(inst)


def available_cores() -> int:
    try:
        return len(os.sched_getaffinity(0))  # respects Docker --cpuset-cpus
    except (AttributeError, OSError):
        return os.cpu_count() or 1


def worker_threads(enabled: bool) -> int:
    """Paper's default (-1) uses half of the physical cores; "multi-core" uses all cores but one."""
    return max(1, available_cores() - 1) if enabled else -1


def apply_paper_threads(inst: "ServerInstance") -> bool:
    """Write chunk-system.worker-threads into config/paper-global.yml (only once Paper created it)."""
    from .squaremap import _yaml_set
    path = inst.dir / "config" / "paper-global.yml"
    if not path.is_file():
        return False
    text = path.read_text(encoding="utf-8")
    value = str(worker_threads(inst.cfg.chunky.multicore))
    new = _yaml_set(text, ("chunk-system", "worker-threads"), value)
    if new != text:
        atomic_write_text(path, new)
    return True


async def set_multicore(inst: "ServerInstance", enabled: bool) -> None:
    """Fabric/Quilt: install or remove C2ME. Paper/Purpur: only the config value (see prepare)."""
    t = inst.cfg.type
    if t in ("fabric", "quilt"):
        if enabled and not c2me_installed(inst):
            version = await _latest(C2ME_ID, inst, ["fabric"], refresh=True)
            if not version:
                raise ModpackError(f"C2ME nie ma wersji dla Minecrafta {inst.cfg.mc_version} – wielowątkowe generowanie jest niedostępne")
            await content.Installer(inst, "").modrinth(C2ME_ID, version_id=version["id"])
        elif not enabled:
            for p in _files(inst, _C2ME_JAR):
                content.remove(inst, p.name.removesuffix(".disabled"))
    elif t in ("paper", "purpur"):
        await asyncio.to_thread(apply_paper_threads, inst)
    elif enabled:
        raise ModpackError("Wielowątkowe generowanie jest dostępne tylko na Paper, Purpur, Fabric i Quilt")


def multicore_info(inst: "ServerInstance") -> dict:
    t = inst.cfg.type
    if t in ("paper", "purpur"):
        return {"available": True, "method": "paper", "threads": worker_threads(True),
                "default_threads": max(1, available_cores() // 4), "cores": available_cores()}
    if t in ("fabric", "quilt"):
        return {"available": True, "method": "c2me", "installed": c2me_installed(inst), "cores": available_cores()}
    return {"available": False, "method": None, "cores": available_cores(),
            "reason": "Forge i NeoForge nie mają sprawdzonego sposobu na wielowątkowe generowanie świata."}


# ====================================================================== running tasks

class ChunkyRunner:
    """Follows Chunky's task through its log lines and applies "only while nobody plays"."""

    def __init__(self, inst: "ServerInstance"):
        self.inst = inst
        self._file = inst.dir / ".minenode" / "chunky.json"
        self.task: dict = self._load()   # persisted: the task the user started from the panel
        self.running = False              # Chunky is generating right now
        self.auto_paused = False          # paused by MineNode because somebody plays
        self._pending: tuple[str, float] | None = None  # (command sent, when) waiting for a confirmation

    # ---------------------------------------------------------------- persistence
    def _load(self) -> dict:
        try:
            data = json.loads(self._file.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self) -> None:
        try:
            self._file.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(self._file, json.dumps(self.task))
        except OSError as exc:
            log.warning("chunky state not saved: %s", exc)

    @property
    def active(self) -> bool:
        """A task the user started that has not finished or been cancelled."""
        return bool(self.task.get("active"))

    def status(self) -> dict:
        t = self.task
        if not t:
            state = "idle"
        elif t.get("finished_at") and not t.get("active"):
            state = "finished"
        elif self.running:
            state = "running"
        elif t.get("active") and t.get("user_paused"):
            state = "paused"
        elif t.get("active"):
            state = "waiting"  # for an empty server, for the server to start or for Chunky to confirm
        else:
            state = "idle"
        return {"state": state, "running": self.running, "auto_paused": self.auto_paused, **t}

    # ---------------------------------------------------------------- commands
    def _send(self, *commands: str) -> None:
        async def run() -> None:
            for cmd in commands:
                await self.inst.send_command(cmd, echo=True)
        self.inst.spawn(run())

    def start(self) -> None:
        s = self.inst.cfg.chunky
        world = world_arg(self.inst, s.world)
        self.task = {"active": True, "user_paused": False, "world": s.world, "world_arg": world,
                     "shape": s.shape, "pattern": s.pattern, "center_x": s.center_x, "center_z": s.center_z,
                     "radius": s.radius, "started": False, "percent": 0.0, "processed": 0, "eta": None,
                     "rate": None, "created_at": time.time(), "finished_at": None}
        self._save()
        self.auto_paused = False
        if self._may_run():
            self._begin()

    def _begin(self) -> None:
        t = self.task
        self._send(f"chunky world {t['world_arg']}", f"chunky shape {t['shape']}", f"chunky pattern {t['pattern']}",
                   f"chunky center {t['center_x']} {t['center_z']}", f"chunky radius {t['radius']}",
                   "chunky quiet 10", "chunky start",
                   # Only needed when an older task exists for this world ("start a new task?").
                   "chunky confirm")
        self._pending = ("start", time.time())

    def pause(self) -> None:
        if not self.active:
            return
        self.task["user_paused"] = True
        self._save()
        if self.running or self._pending:
            self._send("chunky pause")

    def resume(self) -> None:
        if not self.active:
            return
        self.task["user_paused"] = False
        self._save()
        if self._may_run():
            self._continue()

    def cancel(self) -> None:
        self.task = {**self.task, "active": False, "user_paused": False, "cancelled_at": time.time()}
        self._save()
        self.running = self.auto_paused = False
        self._pending = None
        if self.inst.running:
            self._send("chunky cancel", "chunky confirm")

    def _continue(self) -> None:
        if not self.task.get("started"):
            self._begin()
        else:
            self._send("chunky continue")
            self._pending = ("continue", time.time())

    # ---------------------------------------------------------------- decisions
    def _may_run(self) -> bool:
        inst = self.inst
        if not self.active or self.task.get("user_paused") or inst.state != "online":
            return False
        if not inst.cfg.chunky.only_when_empty:
            return True
        return not inst.online and inst.empty_since is not None and time.time() - inst.empty_since >= IDLE_DELAY

    # ---------------------------------------------------------------- events from the server
    def on_line(self, plain: str) -> None:
        for pattern, kind_ in _LOG:
            m = pattern.search(plain)
            if not m:
                continue
            t = self.task
            if kind_ in ("started", "continuing"):
                self.running, self._pending = True, None
                if t:
                    t["started"] = True
                    self._save()
            elif kind_ == "progress":
                self.running = True
                if t:
                    t.update(started=True, processed=int(m.group(2).replace(",", "")),
                             percent=float(m.group(3).replace(",", ".")), eta=m.group(4),
                             rate=float(m.group(5).replace(",", ".")))
            elif kind_ == "finished":
                self.running = self.auto_paused = False
                self._pending = None
                if t:
                    t.update(active=False, user_paused=False, percent=100.0, eta=None,
                             processed=int(m.group(2).replace(",", "")), duration=m.group(4), finished_at=time.time())
                    self._save()
                    self.inst.panel_log(f"Chunky: generowanie świata {t.get('world_arg')} zakończone.")
            elif kind_ in ("paused", "stopped"):
                self.running = False
                self._pending = None
                if t:
                    self._save()
            elif kind_ == "cancelled":
                self.running = False
                self._pending = None
            elif kind_ == "none":
                # Chunky has no saved task (e.g. its files were removed): start it again from the panel.
                self._pending = None
                if t.get("active"):
                    t["started"] = False
                    self._save()
            return

    def on_players(self, count: int) -> None:
        if count <= 0 or not self.inst.cfg.chunky.only_when_empty:
            return
        if self.active and (self.running or self._pending) and not self.task.get("user_paused"):
            self.auto_paused = True
            self._send("chunky pause")
            self.inst.panel_log("Chunky: gracz dołączył – generowanie świata wstrzymane do czasu, aż serwer opustoszeje.")

    def on_stop(self) -> None:
        self.running = False
        self._pending = None
        if self.task.get("percent") is not None:
            self._save()

    def tick(self, now: float) -> None:
        if self._pending and now - self._pending[1] > 60:
            self._pending = None  # no confirmation – try again on the next tick
        if self.running or self._pending or not self._may_run():
            return
        if self.auto_paused:
            self.inst.panel_log("Chunky: serwer jest pusty – wznawiam generowanie świata.")
        self.auto_paused = False
        self._continue()


# ====================================================================== panel state

def _version_key(v: str | None) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v or ""))


def _newer(candidate: str | None, installed: str | None) -> bool:
    """Only a higher version is an update (Modrinth's newest build for an old Minecraft can be older)."""
    return bool(candidate and installed and _version_key(candidate) > _version_key(installed))


async def state(inst: "ServerInstance", refresh: bool = False) -> dict:
    supported = engine_supported(inst)
    info = installed_info(inst)
    latest, error = None, None
    if supported:
        try:
            latest = await latest_version(inst, refresh=refresh)
        except Exception as exc:  # noqa: BLE001 – an unavailable API must not break the tab
            error = str(exc)
    update = bool(info and latest and _newer(latest.get("version_number"), info.get("version_number")))
    runner = inst.chunky_runner if hasattr(inst, "chunky_runner") else None
    return {
        "supported": supported,
        "reason": unsupported_reason(inst),
        "kind": kind(inst),
        "engine": inst.cfg.type,
        "mc_version": inst.cfg.mc_version,
        "installed": info is not None,
        "version": (info or {}).get("version_number"),
        "latest_version": (latest or {}).get("version_number"),
        "available": latest is not None,
        "update_available": update,
        "error": error,
        "settings": inst.cfg.chunky.model_dump(),
        "multicore": multicore_info(inst),
        "task": runner.status() if runner else None,
        "server_running": inst.state == "online",
        "players_online": len(inst.online),
        "worlds": {w: world_arg(inst, w) for w in WORLDS},
    }
