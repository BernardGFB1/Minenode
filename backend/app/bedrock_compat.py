"""Geyser compatibility and managed ViaVersion installation.

Geyser Standalone presents itself to the Java server as the Java version it
supports.  Older servers therefore need a server-side protocol translator.
This module deliberately keeps the policy small and explicit: Paper/Purpur
use ViaVersion, Fabric uses ViaFabric, and unsupported loaders are rejected.
"""
from __future__ import annotations

import json
import logging
import re
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING

from . import net
from .content import load_meta, save_meta

if TYPE_CHECKING:
    from .manager import ServerInstance

log = logging.getLogger("minenode.bedrock.compat")

GEYSER_JAVA_FALLBACK = "26.3"
GEYSER_PROTOCOL_FALLBACK = 777
MODRINTH = "https://api.modrinth.com/v2"
VIAVERSION_PROJECT = "P1OZGk5p"
VIAFABRIC_PROJECT = "YlKdE5VK"
FABRIC_API_PROJECT = "P7dR8mSH"
VIA_JAVA8_URL = (
    "https://ci.viaversion.com/view/ViaVersion/job/ViaVersion-Java8/55/"
    "artifact/build/libs/ViaVersion-5.12.3-SNAPSHOT-downgraded.jar"
)
VIA_JAVA8_SHA256 = "28b2507e320dc8a23935ec852a4b0f977f99c7cd76dd9f2f6652fa0fa6e75"


class CompatibilityError(RuntimeError):
    pass


def version_key(value: str) -> tuple[int, ...]:
    """Semantic-ish key that also handles Minecraft's year-based versions."""
    nums = tuple(int(x) for x in re.findall(r"\d+", str(value)))
    if not nums:
        return (0, 0, 0)
    return nums + (0,) * max(0, 3 - len(nums))


def _jar_kind(path: Path) -> str | None:
    try:
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
            if "fabric.mod.json" in names:
                try:
                    data = json.loads(zf.read("fabric.mod.json"))
                    mid = data.get("id", "")
                    if mid == "viafabric":
                        return "viafabric"
                    if mid == "fabric-api":
                        return "fabric-api"
                    if mid == "viaversion":
                        return "viaversion"
                except (ValueError, KeyError):
                    pass
            if "plugin.yml" in names:
                text = zf.read("plugin.yml").decode("utf-8", "replace").lower()
                if "name: viaversion" in text:
                    return "viaversion"
    except (OSError, zipfile.BadZipFile):
        pass
    return None


def _files(inst: "ServerInstance", directory: str) -> list[Path]:
    root = inst.dir / directory
    return sorted(root.glob("*.jar")) if root.is_dir() else []


def _find(inst: "ServerInstance", kind: str) -> Path | None:
    directory = "plugins" if inst.cfg.type in ("paper", "purpur") else "mods"
    return next((p for p in _files(inst, directory) if _jar_kind(p) == kind), None)


def _base(inst: "ServerInstance", geyser_java: str | None = None) -> dict:
    geyser_java = geyser_java or GEYSER_JAVA_FALLBACK
    mc = getattr(inst.cfg, "mc_version", GEYSER_JAVA_FALLBACK)
    if mc in ("", "?"):
        return {"state": "unsupported", "supported": False, "reason": "Nieznana wersja Minecrafta."}
    if version_key(mc) >= version_key(geyser_java):
        if version_key(mc) == version_key(geyser_java):
            return {"state": "native", "supported": True, "reason": "Serwer obsługuje wersję Geysera bez translatora."}
        return {"state": "unsupported", "supported": False,
                "reason": f"Serwer {mc} jest nowszy niż wersja Java obsługiwana przez Geysera ({geyser_java})."}
    if inst.cfg.type in ("paper", "purpur"):
        return {"state": "viaversion", "supported": True,
                "reason": f"ViaVersion przetłumaczy klienta Geysera {geyser_java} na serwer {mc}."}
    if inst.cfg.type == "fabric" and version_key(mc) >= version_key("1.14.4"):
        return {"state": "viafabric", "supported": True,
                "reason": f"ViaFabric przetłumaczy klienta Geysera {geyser_java} na serwer {mc}."}
    reasons = {
        "vanilla": "Vanilla nie obsługuje pluginów ani modów translatora.",
        "forge": "Brak wspieranego serwerowego ViaVersion dla Forge.",
        "neoforge": "Brak wspieranego serwerowego ViaVersion dla NeoForge.",
        "quilt": "ViaFabric nie ma oficjalnego wsparcia dla Quilt.",
    }
    return {"state": "unsupported", "supported": False,
            "reason": reasons.get(inst.cfg.type, "Ten silnik nie obsługuje translatora ViaVersion.")}


async def state(inst: "ServerInstance", geyser_java: str | None = None) -> dict:
    result = _base(inst, geyser_java)
    result.update({"server_version": inst.cfg.mc_version,
                   "geyser_java_version": geyser_java or GEYSER_JAVA_FALLBACK,
                   "addon_ready": False, "managed": False,
                   "restart_required": False, "addon": None, "addon_version": None})
    if result["state"] not in ("viaversion", "viafabric"):
        return result
    kind = "viaversion" if result["state"] == "viaversion" else "viafabric"
    jar = _find(inst, kind)
    result["addon"] = kind
    if jar:
        result["addon_ready"] = kind != "viafabric" or bool(_find(inst, "fabric-api"))
        meta = load_meta(inst).get("files", {}).get(jar.name, {})
        result["managed"] = meta.get("managed_by") == "bedrock"
        result["addon_version"] = meta.get("version_number") or jar.name
        result["restart_required"] = bool(getattr(inst, "running", False) and
                                           meta.get("installed_at", 0) > getattr(inst, "started_at", 0))
    return result


async def _modrinth_file(project: str, mc: str, loader: str) -> tuple[str, str, str]:
    params = {"loaders": json.dumps([loader]), "game_versions": json.dumps([mc])}
    versions = await net.get_json(f"{MODRINTH}/project/{project}/version", params=params)
    if not versions:
        params.pop("game_versions", None)
        versions = await net.get_json(f"{MODRINTH}/project/{project}/version", params=params)
    if not versions:
        raise CompatibilityError(f"Nie znaleziono dodatku zgodnego z {loader} {mc}.")
    version = next((v for v in versions if v.get("version_type") == "release"), versions[0])
    file = next((f for f in version["files"] if f.get("primary")), version["files"][0])
    return file["url"], file["filename"], version.get("version_number", "")


async def ensure(inst: "ServerInstance", geyser_java: str | None = None) -> dict:
    result = _base(inst, geyser_java)
    if not result["supported"] or result["state"] == "native":
        return await state(inst, geyser_java)
    kind = result["state"]
    directory = "plugins" if kind == "viaversion" else "mods"
    existing = _find(inst, kind)
    if existing and (kind != "viafabric" or _find(inst, "fabric-api")):
        return await state(inst, geyser_java)
    if existing and kind == "viafabric":
        # ViaFabric embeds ViaVersion, but Fabric API remains a separate
        # required dependency on server installations.
        url, filename, version = await _modrinth_file(FABRIC_API_PROJECT, inst.cfg.mc_version, "fabric")
        dest = inst.dir / directory / Path(filename).name
        await net.download(url, dest)
        meta = load_meta(inst)
        meta.setdefault("files", {})[dest.name] = {
            "source": "managed", "title": "fabric-api", "version_number": version,
            "installed_at": __import__("time").time(), "managed_by": "bedrock", "role": "fabric-api",
        }
        save_meta(inst, meta)
        return await state(inst, geyser_java)
    elif kind == "viaversion" and version_key(inst.cfg.mc_version) < version_key("1.17"):
        url, filename, version = VIA_JAVA8_URL, "ViaVersion-5.12.3-SNAPSHOT-downgraded.jar", "5.12.3-SNAPSHOT-java8"
        checksum = VIA_JAVA8_SHA256
    else:
        project = VIAVERSION_PROJECT if kind == "viaversion" else VIAFABRIC_PROJECT
        loader = "paper" if kind == "viaversion" else "fabric"
        url, filename, version = await _modrinth_file(project, inst.cfg.mc_version, loader)
        checksum = None
    dest = inst.dir / directory / Path(filename).name
    inst.panel_log(f"Bedrock: pobieranie {kind} {version}…")
    await net.download(url, dest, sha256=checksum)
    meta = load_meta(inst)
    meta.setdefault("files", {})[dest.name] = {
        "source": "managed", "title": kind, "version_number": version,
        "installed_at": __import__("time").time(), "managed_by": "bedrock", "role": kind,
    }
    save_meta(inst, meta)
    if kind == "viafabric" and not _find(inst, "fabric-api"):
        api_url, api_name, api_version = await _modrinth_file(FABRIC_API_PROJECT, inst.cfg.mc_version, "fabric")
        api_dest = inst.dir / directory / Path(api_name).name
        inst.panel_log(f"Bedrock: pobieranie wymaganego Fabric API {api_version}…")
        await net.download(api_url, api_dest)
        meta = load_meta(inst)
        meta.setdefault("files", {})[api_dest.name] = {
            "source": "managed", "title": "fabric-api", "version_number": api_version,
            "installed_at": __import__("time").time(), "managed_by": "bedrock", "role": "fabric-api",
        }
        save_meta(inst, meta)
    return await state(inst, geyser_java)


def quick_state(inst: "ServerInstance", geyser_java: str | None = None) -> dict:
    """Synchronous state used by routing/API; installation is done by ensure()."""
    result = _base(inst, geyser_java)
    if result["state"] in ("viaversion", "viafabric"):
        kind = result["state"]
        jar = _find(inst, "viaversion" if kind == "viaversion" else kind)
        result["addon_ready"] = bool(jar) and (kind != "viafabric" or bool(_find(inst, "fabric-api")))
        result["addon_version"] = None
        result["managed"] = False
        if jar:
            info = load_meta(inst).get("files", {}).get(jar.name, {})
            result["addon_version"] = info.get("version_number") or jar.name
            result["managed"] = info.get("managed_by") == "bedrock"
            result["restart_required"] = bool(getattr(inst, "running", False) and
                                               info.get("installed_at", 0) > getattr(inst, "started_at", 0))
    else:
        result["addon_ready"] = result["state"] == "native"
    result.update({"server_version": getattr(inst.cfg, "mc_version", GEYSER_JAVA_FALLBACK),
                   "geyser_java_version": geyser_java or GEYSER_JAVA_FALLBACK,
                   "addon": None if result["state"] in ("native", "unsupported") else result["state"]})
    return result
