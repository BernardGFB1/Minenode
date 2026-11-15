"""squaremap integration: one-click install/update, configuration and static web hosting.

squaremap is a live web map for Paper/Purpur/Fabric/Quilt/Forge/NeoForge. The panel installs and
updates it automatically, disables its embedded HTTP server (the panel serves the generated web
directory itself under /map/<server id>/) and exposes a public server page at /<subdomain>/stats.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING

from . import content
from .modpacks import ModpackError
from .store import atomic_write_text

if TYPE_CHECKING:
    from .manager import ServerInstance

log = logging.getLogger("minenode.squaremap")

PROJECT_ID = "PFb7ZqK6"  # squaremap on Modrinth
PROJECT_SLUG = "squaremap"

# squaremap publishes separate builds per mod loader. Quilt runs the Fabric build; Purpur runs Paper.
LOADERS: dict[str, list[str]] = {
    "paper": ["paper"],
    "purpur": ["paper"],
    "fabric": ["fabric"],
    "quilt": ["fabric"],
    "forge": ["forge"],
    "neoforge": ["neoforge"],
}
_CORE_JAR = re.compile(r"^squaremap-(?:paper|folia|fabric|forge|neoforge|sponge)-mc", re.I)
_VERSION_RE = re.compile(r"(\d+(?:\.\d+)+)", re.I)
_VERSION_TTL = 300.0
_version_cache: dict[tuple[str, str], tuple[float, dict, str]] = {}

# The panel owns YAML-written keys only; every other squaremap setting set by the user is preserved.
_CONFIG_DEFAULTS = (
    (("settings", "internal-webserver", "enabled"), "false"),
    (("settings", "internal-webserver", "flush-json-immediately"), "true"),
    (("settings", "web-directory", "path"), "web"),
)


def engine_supported(inst: "ServerInstance") -> bool:
    return inst.cfg.type in LOADERS


def unsupported_reason(inst: "ServerInstance") -> str | None:
    if engine_supported(inst):
        return None
    if inst.cfg.type == "vanilla":
        return "Serwer Vanilla nie obsługuje pluginów ani modów."
    return "Ten silnik nie obsługuje pluginów ani modów."


def data_dir(inst: "ServerInstance") -> Path:
    """Where squaremap keeps config.yml and its web/tiles directories."""
    if inst.cfg.type in ("paper", "purpur"):
        return inst.dir / "plugins" / "squaremap"
    return inst.dir / "squaremap"


def web_dir(inst: "ServerInstance") -> Path:
    return data_dir(inst) / "web"


def rel_to_server(inst: "ServerInstance", path: Path) -> str:
    try:
        return path.relative_to(inst.dir).as_posix()
    except ValueError:
        return path.as_posix()


def public_url(inst: "ServerInstance") -> str:
    return f"/{inst.cfg.subdomain}/stats"


def map_url(inst: "ServerInstance") -> str:
    return f"/map/{inst.cfg.id}/"


def map_available(inst: "ServerInstance") -> bool:
    return inst.cfg.squaremap_enabled and (web_dir(inst) / "index.html").is_file()


# ====================================================================== installed files

def installed_info(inst: "ServerInstance") -> dict | None:
    """The installed squaremap file: Modrinth metadata when known, otherwise a filename scan."""
    d = content.content_dir(inst)
    meta = content.load_meta(inst).get("files", {})
    for name, info in meta.items():
        if (info.get("project_id") == PROJECT_ID and _CORE_JAR.match(name)
                and ((d / name).is_file() or (d / (name + ".disabled")).is_file())):
            return {"file": name, "version_id": info.get("version_id"),
                    "version_number": info.get("version_number"), "managed": True}
    if d.is_dir():
        for p in sorted(d.iterdir(), key=lambda x: x.name.lower()):
            base = p.name[:-9] if p.name.endswith(".disabled") else p.name
            if _CORE_JAR.match(base) and base.endswith(".jar"):
                matches = list(_VERSION_RE.finditer(base))
                return {"file": base, "version_id": None,
                        "version_number": matches[-1].group(1) if matches else None, "managed": False}
    return None


def is_installed(inst: "ServerInstance") -> bool:
    return installed_info(inst) is not None


def _core_jars(inst: "ServerInstance") -> list[Path]:
    d = content.content_dir(inst)
    if not d.is_dir():
        return []
    out = []
    for p in d.iterdir():
        base = p.name[:-9] if p.name.endswith(".disabled") else p.name
        if _CORE_JAR.match(base) and base.endswith(".jar"):
            out.append(p)
    return out


def disable_jar(inst: "ServerInstance") -> None:
    """Keep the plugin files but stop the server from loading squaremap."""
    for p in _core_jars(inst):
        if not p.name.endswith(".jar"):
            continue
        target = p.with_name(p.name + ".disabled")
        if target.exists():  # duplicate from an interrupted rename – keep one disabled copy
            p.unlink(missing_ok=True)
        else:
            p.rename(target)


def enable_jar(inst: "ServerInstance") -> None:
    for p in _core_jars(inst):
        if p.name.endswith(".jar.disabled"):
            target = p.with_name(p.name[:-9])
            if target.exists():
                p.unlink(missing_ok=True)
            else:
                p.rename(target)


def _cleanup_old_jars(inst: "ServerInstance", keep: str) -> None:
    """Remove every other squaremap build after a successful update (old and disabled copies)."""
    for p in _core_jars(inst):
        if p.name != keep:
            p.unlink(missing_ok=True)


# ====================================================================== version resolution

def _release(versions: list[dict]) -> dict | None:
    if not versions:
        return None
    releases = [v for v in versions if v.get("version_type") == "release"]
    return (releases or versions)[0]


async def latest_version(inst: "ServerInstance", refresh: bool = False) -> tuple[dict, str]:
    """Newest squaremap build for this engine/Minecraft version and a compatibility marker."""
    if not engine_supported(inst):
        raise ModpackError(unsupported_reason(inst) or "Ten silnik nie obsługuje squaremap")
    key = (inst.cfg.type, inst.cfg.mc_version)
    hit = _version_cache.get(key)
    if hit and not refresh and time.time() - hit[0] < _VERSION_TTL:
        return hit[1], hit[2]
    loaders = LOADERS[inst.cfg.type]
    try:
        version = _release(await content.modrinth_versions(PROJECT_ID, inst.cfg.mc_version, loaders))
        compat = "ok"
        if version is None:
            # Patch releases are often not tagged yet; accept a build from the same minor line.
            all_versions = await content.modrinth_versions(PROJECT_ID, None, loaders)
            same = [v for v in all_versions
                    if any(content.mc_minor(g) == content.mc_minor(inst.cfg.mc_version)
                           for g in v.get("game_versions", []))]
            version = _release(same)
            compat = "warn"
        if version is None:
            raise ModpackError(f"squaremap nie ma wersji dla {inst.cfg.type} {inst.cfg.mc_version}")
    except Exception:
        if hit:  # a temporary network failure must not break an installed map
            return hit[1], hit[2]
        raise
    _version_cache[key] = (time.time(), version, compat)
    return version, compat


# ====================================================================== install / config

async def install(inst: "ServerInstance", refresh: bool = False) -> dict:
    """Install or update squaremap (and Fabric API where required) and prepare config.yml."""
    if not engine_supported(inst):
        raise ModpackError(unsupported_reason(inst) or "Ten silnik nie obsługuje squaremap")
    version, compat = await latest_version(inst, refresh=refresh)
    installer = content.Installer(inst, "")
    await installer.modrinth(PROJECT_ID, version_id=version["id"])
    info = installed_info(inst) or {}
    if info.get("file"):
        _cleanup_old_jars(inst, info["file"])
    await asyncio.to_thread(write_config, inst)
    return {
        "version": info.get("version_number") or version.get("version_number"),
        "version_id": info.get("version_id") or version.get("id"),
        "compat": compat,
        "installed_files": installer.installed,
    }


async def ensure(inst: "ServerInstance", refresh: bool = False) -> dict | None:
    """Called before a start: update when needed and make sure the embedded HTTP server stays off."""
    if not engine_supported(inst):
        return None
    if not inst.cfg.squaremap_enabled:
        info = installed_info(inst)
        if info:
            # A jar installed through the panel follows the toggle; a manual install is left enabled but
            # its embedded HTTP server is disabled because port 8080 belongs to the panel.
            if info.get("managed"):
                await asyncio.to_thread(disable_jar, inst)
            await asyncio.to_thread(write_config, inst)
        return None
    info = installed_info(inst)
    try:
        version, compat = await latest_version(inst, refresh=refresh)
    except Exception:
        # Modrinth unavailable: keep the previously installed map running.
        if not info:
            raise
        await asyncio.to_thread(enable_jar, inst)
        await asyncio.to_thread(write_config, inst)
        return {"updated": False, "version": info.get("version_number"), "compat": None}
    if info and ((info.get("version_id") and info["version_id"] == version["id"])
                 or (not info.get("version_id") and info.get("version_number") == version.get("version_number"))):
        await asyncio.to_thread(enable_jar, inst)
        await asyncio.to_thread(write_config, inst)
        return {"updated": False, "version": info.get("version_number") or version.get("version_number"),
                "compat": compat}
    result = await install(inst, refresh=refresh)
    return {"updated": True, **result}


# ====================================================================== YAML (limited, dependency-free)

_YAML_KEY = re.compile(r"^(\s*)([^:#][^:]*?)\s*:(?:\s+|$)(.*)$")


def _yaml_key(raw: str) -> tuple[int, str, re.Match] | None:
    if not raw.strip() or raw.lstrip().startswith("#"):
        return None
    m = _YAML_KEY.match(raw)
    if not m:
        return None
    indent = len(raw) - len(raw.lstrip(" "))
    return indent, m.group(2).strip().strip("'\""), m


def _yaml_set(text: str, keys: tuple[str, ...], value: str) -> str:
    """Set a scalar in a block-style YAML file, preserving everything else.

    squaremap writes config.yml with Configurate (block style, two-space indentation). Only the keys
    owned by the panel are touched; comments, ordering and all user settings stay intact.
    """
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    stack: list[tuple[int, str]] = []
    for i, raw in enumerate(lines):
        found = _yaml_key(raw)
        if not found:
            continue
        indent, key, m = found
        while stack and stack[-1][0] >= indent:
            stack.pop()
        stack.append((indent, key))
        if tuple(k for _, k in stack) == keys:
            lines[i] = f"{' ' * indent}{m.group(2).strip()}: {value}"
            return "\n".join(lines).rstrip("\n") + "\n"

    # The leaf does not exist yet: find the deepest existing prefix and append the missing block.
    prefix: tuple[str, ...] = ()
    block_start = -1
    block_indent = -1
    stack = []
    for i, raw in enumerate(lines):
        found = _yaml_key(raw)
        if not found:
            continue
        indent, key, _ = found
        while stack and stack[-1][0] >= indent:
            stack.pop()
        stack.append((indent, key))
        current = tuple(k for _, k in stack)
        if current == keys[:len(current)] and len(current) > len(prefix):
            prefix, block_start, block_indent = current, i, indent
    if block_start < 0:
        if lines and lines[-1] != "":
            lines.append("")
        for n, key in enumerate(keys):
            suffix = f" {value}" if n == len(keys) - 1 else ""
            lines.append("  " * n + f"{key}:{suffix}")
        return "\n".join(lines).rstrip("\n") + "\n"

    end = block_start
    for j in range(block_start + 1, len(lines)):
        raw = lines[j]
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        if indent <= block_indent:
            break
        end = j
    step = block_indent // (len(prefix) - 1) if len(prefix) > 1 else 2
    new_lines = []
    for n, key in enumerate(keys[len(prefix):]):
        level = len(prefix) + n
        suffix = f" {value}" if n == len(keys) - len(prefix) - 1 else ""
        new_lines.append(" " * (level * max(1, step)) + f"{key}:{suffix}")
    lines[end + 1:end + 1] = new_lines
    return "\n".join(lines).rstrip("\n") + "\n"


def write_config(inst: "ServerInstance") -> None:
    """Create or update squaremap's config.yml: internal web server off, JSON written immediately."""
    d = data_dir(inst)
    d.mkdir(parents=True, exist_ok=True)
    path = d / "config.yml"
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        text = ""
    if not re.search(r"^config-version\s*:", text, re.M):
        text = "config-version: 3\n" + text
    for keys, value in _CONFIG_DEFAULTS:
        text = _yaml_set(text, keys, value)
    atomic_write_text(path, text)


# ====================================================================== panel state

async def state(inst: "ServerInstance", refresh: bool = False) -> dict:
    supported = engine_supported(inst)
    info = installed_info(inst)
    latest: dict | None = None
    error = None
    compat = None
    if supported:
        try:
            latest, compat = await latest_version(inst, refresh=refresh)
        except Exception as exc:  # noqa: BLE001 – an unavailable API must not break the settings page
            error = str(exc)
    update_available = bool(
        info and latest and (
            (info.get("version_id") and info["version_id"] != latest.get("id"))
            or (not info.get("version_id") and info.get("version_number")
                and info["version_number"] != latest.get("version_number"))
        )
    )
    return {
        "supported": supported,
        "engine": inst.cfg.type,
        "reason": unsupported_reason(inst),
        "available": latest is not None,
        "error": error,
        "installed": info is not None,
        "enabled": inst.cfg.squaremap_enabled,
        "version": (info or {}).get("version_number"),
        "latest_version": (latest or {}).get("version_number"),
        "update_available": update_available,
        "compat": compat,
        "map_available": map_available(inst),
        "map_url": map_url(inst),
        "public_url": public_url(inst),
        "data_dir": rel_to_server(inst, data_dir(inst)),
        "web_dir": rel_to_server(inst, web_dir(inst)),
        "restart_required": False,
        "idle_render": bool(getattr(inst.cfg, "squaremap_idle_render", True)),
        "render": inst.map_render.status() if hasattr(inst, "map_render") else None,
    }


# ====================================================================== render while nobody plays

IDLE_RENDER_DELAY = 120   # seconds without players before a full render starts (or resumes)
START_TIMEOUT = 45        # a world that does not confirm the command in time is skipped until restart
WORLDS = ("minecraft:overworld", "minecraft:the_nether", "minecraft:the_end")

# squaremap's log lines (common/config/Messages.java). <world> is the dimension identifier.
_RENDER_LOG = (
    (re.compile(r"Started full map render for (\S+)"), "started"),
    (re.compile(r"Rendering map for (\S+) has been resumed"), "started"),
    (re.compile(r"A render is already in progress on (\S+)"), "started"),
    (re.compile(r"Finished rendering map for (\S+)"), "finished"),
    (re.compile(r"Rendering map for (\S+) has been interrupted"), "interrupted"),
    (re.compile(r"Render cancelled for (\S+)"), "interrupted"),
    (re.compile(r"Unpaused renders for (\S+)"), "unpaused"),
    (re.compile(r"Paused renders for (\S+)"), "paused"),
)
_LABEL_RE = re.compile(r"^\s*main-command-label\s*:\s*['\"]?([A-Za-z0-9_-]+)", re.M)


def _world_id(raw: str) -> str:
    w = raw.strip().rstrip(".!,").lower()
    return w if ":" in w else f"minecraft:{w}"


def command_label(inst: "ServerInstance") -> str:
    try:
        m = _LABEL_RE.search((data_dir(inst) / "config.yml").read_text(encoding="utf-8"))
    except OSError:
        m = None
    return m.group(1) if m else "squaremap"


class IdleRender:
    """Full squaremap renders run only while the server is empty.

    After players have been on the server, every world is queued for a full render. Once nobody has
    been online for IDLE_RENDER_DELAY seconds, the worlds are rendered one after another. When a
    player joins, the running render is paused (squaremap keeps its progress) and it continues the
    next time the server is empty. Interrupted renders resume from squaremap's own checkpoint.
    """

    def __init__(self, inst: "ServerInstance"):
        self.inst = inst
        self.rendering: str | None = None
        self.paused = False
        self.awaiting: tuple[str, float] | None = None
        self.skipped: set[str] = set()
        self._file = inst.dir / ".minenode" / "squaremap-render.json"
        self.pending: list[str] = self._load()

    # ---------------------------------------------------------------- persistence
    def _load(self) -> list[str]:
        import json
        try:
            data = json.loads(self._file.read_text(encoding="utf-8"))
            return [w for w in data.get("pending", []) if isinstance(w, str)]
        except (OSError, ValueError, AttributeError):
            return list(WORLDS)  # never rendered by the panel yet

    def _save(self) -> None:
        import json
        try:
            self._file.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(self._file, json.dumps({"pending": self.pending}))
        except OSError as exc:
            log.warning("squaremap render state not saved: %s", exc)

    # ---------------------------------------------------------------- state
    def active(self) -> bool:
        cfg = self.inst.cfg
        return bool(cfg.squaremap_enabled and getattr(cfg, "squaremap_idle_render", True)
                    and engine_supported(self.inst))

    def status(self) -> dict:
        return {"rendering": self.rendering, "paused": self.paused, "pending": list(self.pending),
                "delay": IDLE_RENDER_DELAY}

    def _send(self, *args: str) -> None:
        cmd = " ".join((command_label(self.inst),) + args)
        self.inst.spawn(self.inst.send_command(cmd, echo=False))

    # ---------------------------------------------------------------- events from the server
    def on_line(self, plain: str) -> None:
        for pattern, kind in _RENDER_LOG:
            m = pattern.search(plain)
            if not m:
                continue
            world = _world_id(m.group(1))
            if kind == "started":
                self.rendering, self.awaiting = world, None
            elif kind == "finished":
                if world in self.pending:
                    self.pending.remove(world)
                    self._save()
                if self.rendering == world:
                    self.rendering, self.paused = None, False
                    self.inst.panel_log(f"squaremap: mapa {world} jest wyrenderowana.")
            elif kind == "interrupted":
                if self.rendering == world:
                    self.rendering, self.paused = None, False
            elif kind == "paused":
                self.paused = True
            elif kind == "unpaused":
                self.paused = False
            return

    def on_players(self, count: int) -> None:
        if count <= 0:
            return
        if self.pending != list(WORLDS):  # somebody plays: the map will need a fresh render
            self.pending = list(WORLDS)
            self._save()
        if self.rendering and not self.paused and self.inst.running:
            self.paused = True  # confirmed by "Paused renders for …"
            self._send("pauserender", self.rendering)
            self.inst.panel_log("squaremap: gracz dołączył – renderowanie mapy wstrzymane do czasu, aż serwer opustoszeje.")

    def on_stop(self) -> None:
        self.rendering, self.paused, self.awaiting = None, False, None
        self.skipped.clear()

    # ---------------------------------------------------------------- timer (manager scheduler)
    def tick(self, now: float) -> None:
        inst = self.inst
        if not self.active() or inst.state != "online" or inst.online:
            return
        if not inst.empty_since or now - inst.empty_since < IDLE_RENDER_DELAY:
            return
        if self.awaiting:
            world, sent = self.awaiting
            if now - sent < START_TIMEOUT:
                return
            self.skipped.add(world)  # unknown/disabled dimension or squaremap not loaded
            self.awaiting = None
        if self.rendering:
            if self.paused:
                self.paused = False
                self._send("pauserender", self.rendering)  # the command toggles: resumes the render
                inst.panel_log(f"squaremap: serwer jest pusty – wznawiam renderowanie mapy {self.rendering}.")
            return
        world = next((w for w in self.pending if w not in self.skipped), None)
        if world is None or not is_installed(inst):
            return
        self.awaiting = (world, now)
        self._send("fullrender", world)
        inst.panel_log(f"squaremap: nikt nie gra – renderuję mapę {world}.")
