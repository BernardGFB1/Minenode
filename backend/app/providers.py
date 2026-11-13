"""Server engine providers: version discovery and installation of server software.

Supported: Vanilla, Paper, Purpur, Fabric, Quilt, Forge, NeoForge.
"""
from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import net
from .java import heuristic_java_for, _os
from .models import LaunchSpec

Log = Callable[[str], None]

MOJANG_MANIFEST = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
PAPER_FILL = "https://fill.papermc.io/v3/projects"
PAPER_V2 = "https://api.papermc.io/v2/projects"
PURPUR = "https://api.purpurmc.org/v2/purpur"
FABRIC_META = "https://meta.fabricmc.net/v2"
QUILT_META = "https://meta.quiltmc.org/v3"
FORGE_MAVEN = "https://maven.minecraftforge.net/net/minecraftforge/forge"
FORGE_PROMOS = "https://files.minecraftforge.net/net/minecraftforge/forge/promotions_slim.json"
NEO_MAVEN = "https://maven.neoforged.net/releases/net/neoforged/neoforge"
NEO_VERSIONS = "https://maven.neoforged.net/api/maven/versions/releases/net/neoforged/neoforge"

ENGINES = {
    "vanilla": {"name": "Vanilla", "desc": "Oficjalny serwer Mojang, bez modyfikacji.", "mods": False, "plugins": False},
    "paper": {"name": "Paper", "desc": "Wydajny fork Spigota z obsługą pluginów Bukkit.", "mods": False, "plugins": True},
    "purpur": {"name": "Purpur", "desc": "Paper z dodatkowymi opcjami konfiguracji rozgrywki.", "mods": False, "plugins": True},
    "fabric": {"name": "Fabric", "desc": "Lekki, nowoczesny loader modów.", "mods": True, "plugins": False},
    "quilt": {"name": "Quilt", "desc": "Fork Fabrica, kompatybilny z większością jego modów.", "mods": True, "plugins": False},
    "forge": {"name": "Forge", "desc": "Klasyczny loader dla dużych paczek modów.", "mods": True, "plugins": False},
    "neoforge": {"name": "NeoForge", "desc": "Rozwijany następca Forge'a (1.20.1+).", "mods": True, "plugins": False},
    "custom": {"name": "Custom JAR", "desc": "Własny plik .jar silnika – panel niczego nie pobiera, wgrywasz go w zakładce Pliki.",
               "mods": False, "plugins": False, "custom": True},
}

_cache: dict[str, tuple[float, object]] = {}


async def _cached(key: str, ttl: float, fn):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    value = await fn()
    _cache[key] = (time.time(), value)
    return value


def vkey(v: str) -> tuple:
    """Sort key for loosely-semantic version strings."""
    main = re.split(r"[-+ ]", v, maxsplit=1)
    nums = tuple(int(x) for x in re.findall(r"\d+", main[0])) or (0,)
    pre = 0 if len(main) == 1 else -1  # pre-releases sort below releases
    return nums + (pre,)


@dataclass
class InstallContext:
    server_dir: Path
    mc_version: str
    loader_version: str | None
    java: Path
    log: Log


# ---------------------------------------------------------------- Mojang metadata

async def mojang_manifest() -> dict:
    return await _cached("mojang", 1800, lambda: net.get_json(MOJANG_MANIFEST))


async def mojang_version(mc: str) -> dict | None:
    manifest = await mojang_manifest()
    entry = next((v for v in manifest["versions"] if v["id"] == mc), None)
    if not entry:
        return None
    return await _cached(f"mojang:{mc}", 86400, lambda: net.get_json(entry["url"]))


async def required_java(mc: str) -> int:
    try:
        meta = await mojang_version(mc)
        if meta and meta.get("javaVersion"):
            return int(meta["javaVersion"]["majorVersion"])
    except Exception:
        pass
    return heuristic_java_for(mc)


# ---------------------------------------------------------------- Version discovery

async def game_versions(engine: str, snapshots: bool = False) -> list[dict]:
    if engine in ("vanilla", "custom"):  # for a custom JAR the version only picks the Java runtime
        m = await mojang_manifest()
        return [
            {"id": v["id"], "stable": v["type"] == "release", "date": v.get("releaseTime")}
            for v in m["versions"]
            if v["type"] == "release" or (snapshots and v["type"] == "snapshot")
        ]
    if engine in ("paper", "purpur"):
        versions = await (_paper_versions() if engine == "paper" else _purpur_versions())
        return [{"id": v, "stable": not re.search(r"pre|rc|snapshot", v)} for v in versions]
    if engine == "fabric":
        data = await _cached("fabric:game", 3600, lambda: net.get_json(f"{FABRIC_META}/versions/game"))
        return [{"id": v["version"], "stable": v["stable"]} for v in data if snapshots or v["stable"]]
    if engine == "quilt":
        data = await _cached("quilt:game", 3600, lambda: net.get_json(f"{QUILT_META}/versions/game"))
        return [{"id": v["version"], "stable": v["stable"]} for v in data if snapshots or v["stable"]]
    if engine == "forge":
        versions = await _forge_all()
        mcs = sorted({v.split("-", 1)[0] for v in versions}, key=vkey, reverse=True)
        return [{"id": v, "stable": True} for v in mcs]
    if engine == "neoforge":
        versions = await _neo_all()
        mcs = sorted({_neo_to_mc(v) for v in versions if _neo_to_mc(v)}, key=vkey, reverse=True)
        return [{"id": v, "stable": True} for v in mcs]
    raise ValueError(f"Nieznany silnik: {engine}")


async def loader_versions(engine: str, mc: str) -> list[dict]:
    if engine in ("vanilla", "custom"):
        return []
    if engine == "paper":
        builds = await _paper_builds(mc)
        return [{"id": str(b["id"]), "stable": b["stable"]} for b in builds[:40]]
    if engine == "purpur":
        data = await net.get_json(f"{PURPUR}/{mc}")
        return [{"id": b, "stable": True} for b in reversed(data["builds"]["all"][-40:])]
    if engine == "fabric":
        data = await net.get_json(f"{FABRIC_META}/versions/loader/{mc}")
        return [{"id": d["loader"]["version"], "stable": d["loader"].get("stable", True)} for d in data[:40]]
    if engine == "quilt":
        data = await net.get_json(f"{QUILT_META}/versions/loader/{mc}")
        return [{"id": d["loader"]["version"], "stable": "beta" not in d["loader"]["version"]} for d in data[:40]]
    if engine == "forge":
        versions = [v for v in await _forge_all() if v.startswith(mc + "-")]
        promos = await _forge_promos()
        rec = promos.get(f"{mc}-recommended")
        out = sorted((v[len(mc) + 1:] for v in versions), key=vkey, reverse=True)
        return [{"id": v, "stable": v == rec, "recommended": v == rec} for v in out[:60]]
    if engine == "neoforge":
        versions = [v for v in await _neo_all() if _neo_to_mc(v) == mc]
        out = sorted(versions, key=vkey, reverse=True)
        return [{"id": v, "stable": "beta" not in v and "alpha" not in v} for v in out[:60]]
    raise ValueError(f"Nieznany silnik: {engine}")


async def default_loader(engine: str, mc: str) -> str | None:
    if engine in ("vanilla", "custom"):
        return None
    if engine == "forge":
        promos = await _forge_promos()
        v = promos.get(f"{mc}-recommended") or promos.get(f"{mc}-latest")
        if v:
            return v
    versions = await loader_versions(engine, mc)
    if not versions:
        raise RuntimeError(f"Brak wersji {ENGINES[engine]['name']} dla Minecraft {mc}")
    stable = [v for v in versions if v.get("stable")]
    return (stable or versions)[0]["id"]


# --- Paper / Purpur

async def _paper_versions() -> list[str]:
    async def fetch():
        try:
            data = await net.get_json(f"{PAPER_FILL}/paper")
            groups = data.get("versions", {})
            flat = [v for vs in groups.values() for v in vs] if isinstance(groups, dict) else list(groups)
        except Exception:
            data = await net.get_json(f"{PAPER_V2}/paper")
            flat = data["versions"]
        return sorted(set(flat), key=vkey, reverse=True)

    return await _cached("paper:versions", 1800, fetch)


async def _paper_builds(mc: str) -> list[dict]:
    """Normalised build list (newest first): {id, stable, name, url, sha256}."""
    try:
        data = await net.get_json(f"{PAPER_FILL}/paper/versions/{mc}/builds")
        builds = []
        for b in data:
            dl = (b.get("downloads") or {}).get("server:default")
            if not dl:
                continue
            builds.append({
                "id": b["id"], "stable": b.get("channel", "").upper() in ("STABLE", "RECOMMENDED"),
                "name": dl["name"], "url": dl["url"], "sha256": (dl.get("checksums") or {}).get("sha256"),
            })
    except Exception:
        data = await net.get_json(f"{PAPER_V2}/paper/versions/{mc}/builds")
        builds = []
        for b in data["builds"]:
            app = b["downloads"]["application"]
            builds.append({
                "id": b["build"], "stable": b.get("channel") == "default", "name": app["name"],
                "url": f"{PAPER_V2}/paper/versions/{mc}/builds/{b['build']}/downloads/{app['name']}",
                "sha256": app.get("sha256"),
            })
    builds.sort(key=lambda b: int(b["id"]), reverse=True)
    return builds


async def _purpur_versions() -> list[str]:
    async def fetch():
        data = await net.get_json(PURPUR)
        return sorted(data["versions"], key=vkey, reverse=True)

    return await _cached("purpur:versions", 1800, fetch)


# --- Forge / NeoForge

async def _forge_all() -> list[str]:
    async def fetch():
        xml = await net.get_text(f"{FORGE_MAVEN}/maven-metadata.xml")
        return re.findall(r"<version>([^<]+)</version>", xml)

    return await _cached("forge:all", 3600, fetch)


async def _forge_promos() -> dict:
    async def fetch():
        data = await net.get_json(FORGE_PROMOS)
        return data.get("promos", {})

    return await _cached("forge:promos", 3600, fetch)


async def _neo_all() -> list[str]:
    async def fetch():
        data = await net.get_json(NEO_VERSIONS)
        return data.get("versions", [])

    return await _cached("neo:all", 3600, fetch)


def _neo_to_mc(v: str) -> str | None:
    base = v.split("-", 1)[0]
    parts = base.split(".")
    if not all(p.isdigit() for p in parts) or len(parts) < 3:
        return None
    if int(parts[0]) >= 25:  # year-based: 26.1.0.x -> 26.1 ; 26.1.1.x -> 26.1.1
        mc = f"{parts[0]}.{parts[1]}"
        if len(parts) >= 4 and parts[2] != "0":
            mc += f".{parts[2]}"
        return mc
    mc = f"1.{parts[0]}"
    if parts[1] != "0":
        mc += f".{parts[1]}"
    return mc


# ---------------------------------------------------------------- Installation

async def _run(cmd: list[str], cwd: Path, log: Log) -> int:
    log("$ " + " ".join(cmd))
    proc = await asyncio.create_subprocess_exec(
        *cmd, cwd=str(cwd), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        stdin=asyncio.subprocess.DEVNULL,
    )
    assert proc.stdout
    last = 0.0
    skipped = 0
    async for raw in proc.stdout:
        line = raw.decode("utf-8", "replace").rstrip()
        # Installers are extremely chatty while downloading libraries – throttle those lines.
        if re.search(r"(Downloading|Considering|Extracting|Checksum validated|File exists)", line):
            if time.time() - last < 0.5:
                skipped += 1
                continue
            last = time.time()
        log("  " + line)
    code = await proc.wait()
    if skipped:
        log(f"  (pominięto {skipped} linii postępu)")
    return code


def _progress_logger(log: Log, label: str):
    last = [-25]

    def prog(done: int, total: int | None) -> None:
        if total:
            pct = int(done * 100 / total)
            if pct >= last[0] + 25:
                last[0] = pct
                log(f"  {label}: {pct}%")

    return prog


async def install(engine: str, ctx: InstallContext) -> tuple[LaunchSpec, str | None]:
    ctx.server_dir.mkdir(parents=True, exist_ok=True)
    loader = ctx.loader_version or await default_loader(engine, ctx.mc_version)
    fn = {
        "vanilla": _install_vanilla, "paper": _install_paper, "purpur": _install_purpur,
        "fabric": _install_fabric, "quilt": _install_quilt, "forge": _install_forge, "neoforge": _install_neoforge,
        "custom": _install_custom,
    }[engine]
    launch = await fn(ctx, loader)
    return launch, loader


async def _install_custom(ctx: InstallContext, _loader) -> LaunchSpec:
    """Nothing to download: the user uploads their own server JAR. The file is picked at start."""
    jars = sorted(p.name for p in ctx.server_dir.glob("*.jar") if p.is_file())
    if jars:
        ctx.log(f"Pliki .jar w folderze serwera: {', '.join(jars)}")
    else:
        ctx.log("Wgraj plik .jar silnika do głównego folderu serwera (zakładka Pliki) i uruchom serwer.")
    return LaunchSpec(kind="jar", target="")  # empty: detected from the folder on every start


async def _install_vanilla(ctx: InstallContext, _loader) -> LaunchSpec:
    meta = await mojang_version(ctx.mc_version)
    if not meta:
        raise RuntimeError(f"Nieznana wersja Minecraft: {ctx.mc_version}")
    server = (meta.get("downloads") or {}).get("server")
    if not server:
        raise RuntimeError(f"Minecraft {ctx.mc_version} nie ma oficjalnego serwera")
    ctx.log(f"Pobieranie serwera Vanilla {ctx.mc_version}…")
    await net.download(server["url"], ctx.server_dir / "server.jar", sha1=server.get("sha1"),
                       progress=_progress_logger(ctx.log, "server.jar"))
    return LaunchSpec(kind="jar", target="server.jar")


async def _install_paper(ctx: InstallContext, build: str) -> LaunchSpec:
    builds = await _paper_builds(ctx.mc_version)
    b = next((x for x in builds if str(x["id"]) == str(build)), None)
    if not b:
        raise RuntimeError(f"Nie znaleziono buildu Paper {build} dla {ctx.mc_version}")
    ctx.log(f"Pobieranie Paper {ctx.mc_version} build #{b['id']}…")
    _remove_jars(ctx.server_dir, "paper-")
    await net.download(b["url"], ctx.server_dir / b["name"], sha256=b.get("sha256"),
                       progress=_progress_logger(ctx.log, b["name"]))
    return LaunchSpec(kind="jar", target=b["name"])


async def _install_purpur(ctx: InstallContext, build: str) -> LaunchSpec:
    name = f"purpur-{ctx.mc_version}-{build}.jar"
    ctx.log(f"Pobieranie Purpur {ctx.mc_version} build #{build}…")
    _remove_jars(ctx.server_dir, "purpur-")
    await net.download(f"{PURPUR}/{ctx.mc_version}/{build}/download", ctx.server_dir / name,
                       progress=_progress_logger(ctx.log, name))
    return LaunchSpec(kind="jar", target=name)


async def _install_fabric(ctx: InstallContext, loader: str) -> LaunchSpec:
    installers = await net.get_json(f"{FABRIC_META}/versions/installer")
    inst = next((i for i in installers if i.get("stable")), installers[0])["version"]
    ctx.log(f"Pobieranie Fabric loader {loader} (installer {inst}) dla {ctx.mc_version}…")
    url = f"{FABRIC_META}/versions/loader/{ctx.mc_version}/{loader}/{inst}/server/jar"
    await net.download(url, ctx.server_dir / "fabric-server-launch.jar")
    # Pre-download the vanilla jar so the first start is fast and works offline.
    try:
        await _install_vanilla(ctx, None)
    except Exception as exc:  # the launcher can fetch it itself
        ctx.log(f"  (vanilla server.jar zostanie pobrany przy starcie: {exc})")
    return LaunchSpec(kind="jar", target="fabric-server-launch.jar")


async def _install_quilt(ctx: InstallContext, loader: str) -> LaunchSpec:
    installers = await net.get_json(f"{QUILT_META}/versions/installer")
    inst = installers[0]
    jar = ctx.server_dir / "quilt-installer.jar"
    ctx.log(f"Pobieranie instalatora Quilt {inst['version']}…")
    await net.download(inst["url"], jar)
    code = await _run(
        [str(ctx.java), "-jar", jar.name, "install", "server", ctx.mc_version, loader,
         "--download-server", f"--install-dir={ctx.server_dir}"],
        ctx.server_dir, ctx.log,
    )
    jar.unlink(missing_ok=True)
    if code != 0 or not (ctx.server_dir / "quilt-server-launch.jar").exists():
        raise RuntimeError(f"Instalator Quilt zakończył się błędem ({code})")
    return LaunchSpec(kind="jar", target="quilt-server-launch.jar")


async def _install_forge(ctx: InstallContext, loader: str) -> LaunchSpec:
    full = f"{ctx.mc_version}-{loader}"
    all_versions = await _forge_all()
    if full not in all_versions:
        match = next((v for v in all_versions if v.startswith(full)), None)
        if not match:
            raise RuntimeError(f"Nie znaleziono Forge {full}")
        full = match
    url = f"{FORGE_MAVEN}/{full}/forge-{full}-installer.jar"
    return await _run_forge_like_installer(ctx, url, "forge", "net/minecraftforge/forge", full)


async def _install_neoforge(ctx: InstallContext, loader: str) -> LaunchSpec:
    url = f"{NEO_MAVEN}/{loader}/neoforge-{loader}-installer.jar"
    return await _run_forge_like_installer(ctx, url, "neoforge", "net/neoforged/neoforge", loader)


async def _run_forge_like_installer(ctx: InstallContext, url: str, prefix: str, group: str, full: str) -> LaunchSpec:
    installer = ctx.server_dir / f"{prefix}-installer.jar"
    ctx.log(f"Pobieranie instalatora {prefix} {full}…")
    await net.download(url, installer, progress=_progress_logger(ctx.log, installer.name))
    ctx.log("Uruchamianie instalatora (pobieranie bibliotek może potrwać kilka minut)…")
    code = await _run([str(ctx.java), "-jar", installer.name, "--installServer"], ctx.server_dir, ctx.log)
    installer.unlink(missing_ok=True)
    for leftover in ("installer.log", f"{installer.name}.log"):
        (ctx.server_dir / leftover).unlink(missing_ok=True)
    if code != 0:
        raise RuntimeError(f"Instalator {prefix} zakończył się błędem ({code})")
    launch = detect_forge_launch(ctx.server_dir, prefix, group, full)
    if not launch:
        raise RuntimeError("Nie udało się wykryć sposobu uruchomienia po instalacji")
    return launch


def detect_forge_launch(server_dir: Path, prefix: str, group: str, full: str | None) -> LaunchSpec | None:
    args_name = "win_args.txt" if _os() == "windows" else "unix_args.txt"
    base = server_dir / "libraries" / group
    if full and (base / full / args_name).exists():
        return LaunchSpec(kind="argsfile", target=str((base / full / args_name).relative_to(server_dir)))
    found = sorted(base.glob(f"*/{args_name}"), key=lambda p: vkey(p.parent.name), reverse=True) if base.exists() else []
    if found:
        return LaunchSpec(kind="argsfile", target=str(found[0].relative_to(server_dir)))
    jars = [j for j in server_dir.glob(f"{prefix}-*.jar") if "installer" not in j.name]
    for pref in ("-shim.jar", "-universal.jar", ".jar"):
        for j in jars:
            if j.name.endswith(pref):
                return LaunchSpec(kind="jar", target=j.name)
    return None


def _remove_jars(server_dir: Path, prefix: str) -> None:
    for j in server_dir.glob(f"{prefix}*.jar"):
        j.unlink(missing_ok=True)
