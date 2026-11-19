"""Server-side LOD support: distant terrain sent to players with Distant Horizons or Voxy.

* Distant Horizons: the DH mod itself on Fabric/Quilt/Forge/NeoForge servers, the "Distant Horizons
  Support" plugin (DHSupport) on Paper/Purpur.
* Voxy: "LOD Server Support" (LSS) by its original author – a mod on Fabric/Quilt/NeoForge and a
  plugin on Paper/Purpur. Voxy itself is a client mod. "Voxy Server Side" (VSS) is the same code
  republished under another name; both brands talk the same protocol, so players may use either.
  A VSS jar installed by older panel versions is replaced by LSS (LSS adopts its settings file).

Players still need the client mods (Distant Horizons, or Voxy + LOD Server Support / Voxy Server Side). The panel writes
conservative settings so the LOD service stays light: a moderate radius, few worker threads, low
request rates and bandwidth caps, and no generation of new chunks unless the owner enables it
(pre-generating the world with Chunky is the cheaper way to fill the LODs).
"""
from __future__ import annotations

import asyncio
import re
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from . import content, squaremap
from .managedjar import ManagedJar
from .store import atomic_write_text

if TYPE_CHECKING:
    from .manager import ServerInstance

PLUGIN_ENGINES = ("paper", "purpur")

DH_MOD = ManagedJar("distant_horizons", "Distant Horizons", "uCdwusMi", r"^DistantHorizons-\d[\w.\-+]*\.jar$",
                    {"fabric": ["fabric"], "quilt": ["fabric"], "forge": ["forge"], "neoforge": ["neoforge"]},
                    fallback="never")
DH_PLUGIN = ManagedJar("distant_horizons", "Distant Horizons Support", "IjY7seTG",
                       r"^DistantHorizonsSupport-\d[\w.\-]*\.jar$",
                       {"paper": ["paper"], "purpur": ["paper"]}, fallback="plugin")
# Paper/Folia jars of LSS for 1.21.x refuse to run on another patch release; 26.x jars run on every
# patch release of their line, so only there an older tag may be used.
_LSS_LOADERS = {"fabric": ["fabric"], "quilt": ["fabric"], "neoforge": ["neoforge"], "paper": ["paper"], "purpur": ["paper"]}
VOXY = ManagedJar("voxy", "LOD Server Support", "lKiXKLvv",
                  r"^lod-server-support-(?:paper|fabric|neoforge|folia|sponge)[\w.\-+]*\.jar$", _LSS_LOADERS, fallback="26")
# The same mod under the "Voxy Server Side" name, installed by earlier panel versions.
LEGACY_VSS = ManagedJar("voxy", "Voxy Server Side", "84zcagOb",
                        r"^voxy-server-side-(?:paper|fabric|neoforge|folia|sponge)[\w.\-]*\.jar$", _LSS_LOADERS, fallback="26")


def dh_jar(inst: "ServerInstance") -> ManagedJar:
    return DH_PLUGIN if inst.cfg.type in PLUGIN_ENGINES else DH_MOD


def jars(inst: "ServerInstance") -> dict[str, ManagedJar]:
    return {"distant_horizons": dh_jar(inst), "voxy": VOXY}


def supported(inst: "ServerInstance", key: str) -> bool:
    return jars(inst)[key].engine_supported(inst)


def unsupported_reason(inst: "ServerInstance", key: str) -> Optional[str]:
    if supported(inst, key):
        return None
    if inst.cfg.type in ("vanilla", "custom"):
        return "Wymaga silnika z pluginami lub modami (Paper, Purpur, Fabric, Quilt, Forge, NeoForge)."
    if key == "voxy" and inst.cfg.type == "forge":
        return "LOD Server Support (Voxy) nie ma wersji dla Forge – użyj NeoForge, Fabric albo Paper."
    return "Ten silnik nie jest obsługiwany."


# ====================================================================== limits written by the panel

THREADS = 2          # DH worker threads / DHS scheduler threads
DH_RUN_RATIO = 0.5   # DH threads may use half of their time slice (≈ one core at most)
REQUESTS = 4         # parallel generation requests per player
SYNC_REQUESTS = 10   # parallel login-sync requests per player
DH_PLAYER_KBPS = 300
DH_GLOBAL_KBPS = 1500
VSS_PLAYER_MIBPS = 2
VSS_GLOBAL_MIBPS = 8
VSS_GLOBAL_GENERATIONS = 4


def _nether(distance: int) -> int:
    return min(distance, 64)  # the Nether's ceiling hides most of it anyway


# ---------------------------------------------------------------- Distant Horizons mod (TOML)

def dh_config_path(inst: "ServerInstance") -> Path:
    return inst.dir / "config" / "DistantHorizons.toml"


def _toml_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    return f'"{v}"'


def toml_set(text: str, section: str, key: str, value) -> str:
    """Set ``key`` in ``[section]`` of a simple TOML file (the layout Distant Horizons writes)."""
    lines = text.replace("\r\n", "\n").split("\n")
    header = re.compile(r"^\s*\[([^\[\]]+)\]\s*(?:#.*)?$")
    line_re = re.compile(rf"^(\s*){re.escape(key)}\s*=")
    start, end = None, len(lines)
    for i, raw in enumerate(lines):
        m = header.match(raw)
        if not m:
            continue
        if start is not None:
            end = i
            break
        if m.group(1).strip() == section:
            start = i
    new = f"{key} = {_toml_value(value)}"
    if start is None:
        while lines and lines[-1] == "":
            lines.pop()
        lines += ["", f"[{section}]", "\t" + new]
        return "\n".join(lines) + "\n"
    indent = "\t"
    for i in range(start + 1, end):
        m = line_re.match(lines[i])
        if m:
            lines[i] = m.group(1) + new
            return "\n".join(lines).rstrip("\n") + "\n"
        if lines[i].strip() and not lines[i].lstrip().startswith("#"):
            indent = lines[i][:len(lines[i]) - len(lines[i].lstrip())]
    insert = end
    while insert > start + 1 and not lines[insert - 1].strip():
        insert -= 1
    lines.insert(insert, indent + new)
    return "\n".join(lines).rstrip("\n") + "\n"


def write_dh_mod(inst: "ServerInstance") -> None:
    cfg = inst.cfg.lod
    path = dh_config_path(inst)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        text = ""
    distance = max(256, cfg.distance)  # DH does not accept request distances below 256 chunks
    values = (
        ("server", "generationRequestRateLimit", REQUESTS),
        ("server", "maxGenerationRequestDistance", distance),
        ("server", "syncOnLoadRateLimit", SYNC_REQUESTS),
        ("server", "maxSyncOnLoadRequestDistance", distance),
        ("server", "playerBandwidthLimit", DH_PLAYER_KBPS),
        ("server", "globalBandwidthLimit", DH_GLOBAL_KBPS),
        ("server", "enableAdaptiveTransferSpeed", True),
        ("common.multiThreading", "numberOfThreads", THREADS),
        ("common.multiThreading", "threadRunTimeRatio", DH_RUN_RATIO),
        ("common.worldGenerator", "generatorPlan", "CHUNKS_ONLY"),
        # PRE_EXISTING_ONLY builds LODs from chunks already on disk; FEATURES generates new ones.
        ("common.worldGenerator", "chunkGeneratorMode", "FEATURES" if cfg.generate else "PRE_EXISTING_ONLY"),
    )
    for section, key, value in values:
        text = toml_set(text, section, key, value)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, text)


# ---------------------------------------------------------------- DH Support plugin (YAML)

def dhs_config_path(inst: "ServerInstance") -> Path:
    return inst.dir / "plugins" / "DHSupport" / "config.yml"


def write_dh_plugin(inst: "ServerInstance") -> None:
    cfg = inst.cfg.lod
    path = dhs_config_path(inst)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        # The plugin refuses a config without its required keys: start from the bundled default.
        text = _from_jar(inst, DH_PLUGIN, "config.yml") or ""
    radius = min(cfg.distance, 128)
    values = (
        ("render_distance", cfg.distance),
        ("full_data_request_concurrency_limit", REQUESTS),
        ("real_time_update_radius", min(radius, 64)),
        ("login_data_sync_radius", radius),
        ("login_data_sync_rc_limit", SYNC_REQUESTS),
        ("generate_new_chunks", cfg.generate),
        ("generate_new_chunks_warning", False),
        ("scheduler_threads", THREADS),
        ("lod_refresh_interval", 30),
    )
    for key, value in values:
        text = squaremap._yaml_set(text, (key,), _yaml_value(value))
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, text)


def _yaml_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


# ---------------------------------------------------------------- LOD Server Support for Voxy (YAML)

def lss_config_path(inst: "ServerInstance") -> Path:
    if inst.cfg.type in PLUGIN_ENGINES:
        return inst.dir / "plugins" / "LodServerSupport" / "lss-server-config.yaml"
    return inst.dir / "config" / "lss-server-config.yaml"


def _legacy_vss_config(inst: "ServerInstance") -> Path:
    if inst.cfg.type in PLUGIN_ENGINES:
        return inst.dir / "plugins" / "VoxyServerSide" / "vss-server-config.yaml"
    return inst.dir / "config" / "vss-server-config.yaml"


_DIMENSIONS = ("minecraft:overworld", "minecraft:the_nether", "minecraft:the_end")


def _lss_fresh(inst: "ServerInstance") -> str:
    cfg = inst.cfg.lod
    d = cfg.distance
    return (
        "# Written by MineNode (Add-ons -> Services -> LOD). Missing settings use the mod's defaults.\n"
        "config_version: 1\n"
        "lod:\n  distance:\n"
        f"    default_chunks: {d}\n"
        "    by_dimension:\n"
        f'      "minecraft:overworld": {d}\n'
        f'      "minecraft:the_nether": {_nether(d)}\n'
        f'      "minecraft:the_end": {d}\n'
        "generation:\n"
        f"  enabled: {_yaml_value(cfg.generate)}\n"
        "  concurrency:\n"
        f"    global: {VSS_GLOBAL_GENERATIONS}\n"
        f"    per_player: {min(REQUESTS, VSS_GLOBAL_GENERATIONS)}\n"
        "network:\n  bandwidth:\n"
        f"    per_player_mib_per_second: {VSS_PLAYER_MIBPS}\n"
        f"    global_mib_per_second: {VSS_GLOBAL_MIBPS}\n"
    )


def write_voxy(inst: "ServerInstance") -> None:
    """A strict schema: an unknown key or a bad value switches VSS off, so only documented keys are
    written (lod.distance, generation, network.bandwidth – present since 0.17).

    A settings file left by Voxy Server Side is carried over, so the owner's own edits survive the switch."""
    cfg = inst.cfg.lod
    path = lss_config_path(inst)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        try:
            text = _legacy_vss_config(inst).read_text(encoding="utf-8")
        except FileNotFoundError:
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(path, _lss_fresh(inst))
            return
        path.parent.mkdir(parents=True, exist_ok=True)
    d = cfg.distance
    for keys, value in (
        (("lod", "distance", "default_chunks"), str(d)),
        (("generation", "enabled"), _yaml_value(cfg.generate)),
        (("generation", "concurrency", "global"), str(VSS_GLOBAL_GENERATIONS)),
        (("generation", "concurrency", "per_player"), str(min(REQUESTS, VSS_GLOBAL_GENERATIONS))),
        (("network", "bandwidth", "per_player_mib_per_second"), str(VSS_PLAYER_MIBPS)),
        (("network", "bandwidth", "global_mib_per_second"), str(VSS_GLOBAL_MIBPS)),
    ):
        text = squaremap._yaml_set(text, keys, value)
    # Dimension overrides have quoted keys with a colon, which the generic YAML editor does not parse.
    for dim in _DIMENSIONS:
        value = _nether(d) if dim.endswith("the_nether") else d
        text = re.sub(rf'^(\s*)["\']{re.escape(dim)}["\']\s*:\s*\d+', rf'\g<1>"{dim}": {value}', text, flags=re.M)
    atomic_write_text(path, text)


def _from_jar(inst: "ServerInstance", jar: ManagedJar, name: str) -> Optional[str]:
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


def write_configs(inst: "ServerInstance") -> None:
    cfg = inst.cfg.lod
    if cfg.distant_horizons and supported(inst, "distant_horizons"):
        (write_dh_plugin if inst.cfg.type in PLUGIN_ENGINES else write_dh_mod)(inst)
    if cfg.voxy and supported(inst, "voxy"):
        write_voxy(inst)


# ====================================================================== install / start

async def prepare(inst: "ServerInstance", refresh: bool = False, only: Optional[str] = None) -> dict:
    """Before a start (or when switched on): install/update enabled LOD add-ons, disable the others."""
    results = {}
    cfg = inst.cfg.lod
    # Voxy Server Side from earlier panel versions: LSS is the same mod, two copies must not load together.
    await asyncio.to_thread(LEGACY_VSS.remove_managed, inst)
    for key, jar in jars(inst).items():
        if only and key != only:
            continue
        wanted = getattr(cfg, key) and jar.engine_supported(inst)
        if wanted:
            results[key] = await jar.ensure(inst, refresh=refresh)
        else:
            await asyncio.to_thread(jar.disable, inst)
    await asyncio.to_thread(write_configs, inst)
    return results


async def state(inst: "ServerInstance", refresh: bool = False) -> dict:
    cfg = inst.cfg.lod
    items = {}
    for key, jar in jars(inst).items():
        ok = jar.engine_supported(inst)
        item = {"enabled": getattr(cfg, key), "supported": ok, "reason": unsupported_reason(inst, key),
                "title": jar.title, "kind": "plugin" if inst.cfg.type in PLUGIN_ENGINES else "mod"}
        if ok:
            item.update(await jar.status(inst, refresh=refresh))
        items[key] = item
    return {"distance": cfg.distance, "generate": cfg.generate, "engine": inst.cfg.type,
            "mc_version": inst.cfg.mc_version, "items": items, "restart_required": False}
