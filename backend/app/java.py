"""Automatic Java runtime management (Eclipse Temurin via the Adoptium API)."""
from __future__ import annotations

import asyncio
import platform
import re
import shutil
import sys
import tarfile
import time
import zipfile
from pathlib import Path
from typing import Callable

import httpx

from . import net
from .config import JAVA_DIR, TMP_DIR

Log = Callable[[str], None]

ADOPTIUM = "https://api.adoptium.net/v3"
_locks: dict[int, asyncio.Lock] = {}
_available: tuple[float, list[int]] | None = None
FALLBACK_RELEASES = [8, 11, 17, 21, 25]


def _os() -> str:
    if sys.platform.startswith("linux"):
        return "linux"
    if sys.platform == "darwin":
        return "mac"
    if sys.platform.startswith("win"):
        return "windows"
    return "linux"


def _arch() -> str:
    m = platform.machine().lower()
    if m in ("x86_64", "amd64"):
        return "x64"
    if m in ("aarch64", "arm64"):
        return "aarch64"
    if m.startswith("arm"):
        return "arm"
    return m


def java_exe_name() -> str:
    return "java.exe" if _os() == "windows" else "java"


def heuristic_java_for(mc_version: str) -> int:
    """Best-effort Java requirement when Mojang metadata is unavailable."""
    m = re.match(r"^(\d+)\.(\d+)(?:\.(\d+))?", mc_version)
    if not m:
        return 21
    major, minor, patch = int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)
    if major >= 25:  # new year-based versioning (26.1, ...)
        return 25
    if minor <= 16:
        return 8
    if minor == 17:
        return 16
    if minor < 20 or (minor == 20 and patch <= 4):
        return 17
    return 21


async def available_releases() -> list[int]:
    global _available
    if _available and time.time() - _available[0] < 6 * 3600:
        return _available[1]
    try:
        data = await net.get_json(f"{ADOPTIUM}/info/available_releases")
        lts = sorted(set(data.get("available_lts_releases") or FALLBACK_RELEASES))
        _available = (time.time(), lts)
        return lts
    except Exception:
        return FALLBACK_RELEASES


async def resolve_feature(required: int) -> int:
    """Pick the lowest LTS release that satisfies the requirement."""
    releases = await available_releases()
    for r in releases:
        if r >= required:
            return r
    return releases[-1]


def runtime_dir(feature: int) -> Path:
    return JAVA_DIR / f"temurin-{feature}"


def find_java(root: Path) -> Path | None:
    candidates = [p for p in root.rglob(f"bin/{java_exe_name()}") if p.is_file()]
    if not candidates:
        return None
    return min(candidates, key=lambda p: len(p.parts))


def installed_runtimes() -> list[dict]:
    out = []
    if not JAVA_DIR.exists():
        return out
    for d in sorted(JAVA_DIR.iterdir()):
        m = re.match(r"temurin-(\d+)$", d.name)
        if not m or not d.is_dir():
            continue
        exe = find_java(d)
        if not exe:
            continue
        release = ""
        rel_file = exe.parent.parent / "release"
        if rel_file.exists():
            mm = re.search(r'JAVA_VERSION="([^"]+)"', rel_file.read_text(errors="ignore"))
            release = mm.group(1) if mm else ""
        size = sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
        out.append({"feature": int(m.group(1)), "path": str(exe), "release": release, "size": size})
    return out


def _extract(archive: Path, dest: Path) -> None:
    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as zf:
            for member in zf.namelist():
                target = (dest / member).resolve()
                if not target.is_relative_to(dest.resolve()):
                    raise RuntimeError("Niebezpieczna ścieżka w archiwum Javy")
            zf.extractall(dest)
    else:
        with tarfile.open(archive, "r:gz") as tf:
            tf.extractall(dest, filter="data")


async def ensure_java(required: int, log: Log) -> tuple[int, Path]:
    """Return (feature_version, java_executable), downloading the runtime if needed."""
    feature = await resolve_feature(required)
    lock = _locks.setdefault(feature, asyncio.Lock())
    async with lock:
        existing = runtime_dir(feature)
        exe = find_java(existing) if existing.exists() else None
        if exe:
            return feature, exe

        os_name, arch = _os(), _arch()
        last_error: Exception | None = None
        candidates = [feature] + [r for r in await available_releases() if r > feature]
        for feat in candidates:
            if feat != feature and runtime_dir(feat).exists() and (alt := find_java(runtime_dir(feat))):
                log(f"Uwaga: Java {feature} niedostępna dla tej platformy – używam Java {feat}.")
                return feat, alt
            for image in ("jre", "jdk"):
                url = f"{ADOPTIUM}/binary/latest/{feat}/ga/{os_name}/{arch}/{image}/hotspot/normal/eclipse"
                ext = "zip" if os_name == "windows" else "tar.gz"
                archive = TMP_DIR / f"temurin-{feat}-{image}.{ext}"
                log(f"Pobieranie Java {feat} ({image.upper()}, {os_name}/{arch})…")
                last_pct = -10

                def prog(done: int, total: int | None) -> None:
                    nonlocal last_pct
                    if total:
                        pct = int(done * 100 / total)
                        if pct >= last_pct + 10:
                            last_pct = pct
                            log(f"  Java {feat}: {pct}% ({done // 1048576} / {total // 1048576} MB)")

                try:
                    await net.download(url, archive, progress=prog)
                except httpx.HTTPStatusError as exc:
                    last_error = exc
                    log(f"  Brak buildu {image.upper()} dla Java {feat} ({exc.response.status_code}), próbuję dalej…")
                    continue
                target = runtime_dir(feat)
                staging = TMP_DIR / f"java-extract-{feat}-{int(time.time())}"
                try:
                    log(f"Rozpakowywanie Java {feat}…")
                    await asyncio.to_thread(_extract, archive, staging)
                    if target.exists():
                        shutil.rmtree(target)
                    staging.rename(target)
                finally:
                    archive.unlink(missing_ok=True)
                    if staging.exists():
                        shutil.rmtree(staging, ignore_errors=True)
                exe = find_java(target)
                if not exe:
                    raise RuntimeError(f"Nie znaleziono pliku wykonywalnego Javy w {target}")
                exe.chmod(exe.stat().st_mode | 0o111)
                if feat != feature:
                    log(f"Uwaga: Java {feature} niedostępna dla tej platformy – używam Java {feat}.")
                log(f"Java {feat} gotowa: {exe}")
                return feat, exe
        raise RuntimeError(f"Nie udało się pobrać Javy {feature}: {last_error}")


def remove_runtime(feature: int) -> bool:
    d = runtime_dir(feature)
    if d.exists():
        shutil.rmtree(d)
        return True
    return False
