"""PicoLimbo binary for the authentication lobby: downloaded from GitHub and kept up to date.

The binary is PicoLimbo (https://github.com/Quozul/PicoLimbo) with MineNode's login module, built by
``.github/workflows/picolimbo.yml`` and published as ``picolimbo-*`` releases of the MineNode
repository. The panel installs the newest one into ``/data/auth-lobby/bin`` and checks for a newer
one every few hours. ``MINENODE_PICOLIMBO_BINARY`` points at a local binary instead (no updates).
"""
from __future__ import annotations

import json
import logging
import os
import platform
import re
import shutil
import tarfile
import time
from pathlib import Path

from . import net
from .config import AUTH_LOBBY_DIR, TMP_DIR

log = logging.getLogger("minenode.picolimbo")

REPO = os.environ.get("MINENODE_PICOLIMBO_REPO", "BernardGFB1/Minenode")
TAG_PREFIX = "picolimbo-"
CHECK_INTERVAL = 6 * 3600
MAX_ARCHIVE = 200 * 1024 * 1024
BIN_DIR = AUTH_LOBBY_DIR / "bin"
BINARY = BIN_DIR / "pico_limbo"
VERSION_FILE = BIN_DIR / "version.json"
LOCAL_OVERRIDE = os.environ.get("MINENODE_PICOLIMBO_BINARY")


class PicoLimboError(Exception):
    pass


def arch() -> str | None:
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        return "x86_64"
    if machine in ("aarch64", "arm64"):
        return "aarch64"
    return None


def asset_name(cpu: str) -> str:
    return f"pico_limbo-linux-{cpu}-musl.tar.gz"


def display_version(tag: str | None) -> str | None:
    """picolimbo-v1.15.0+mc26.3-ab12cd34 -> 1.15.0+mc26.3"""
    if not tag:
        return None
    core = tag[len(TAG_PREFIX):] if tag.startswith(TAG_PREFIX) else tag
    core = re.sub(r"-[0-9a-f]{8}$", "", core)  # MineNode patch id
    return core[1:] if core.startswith("v") else core


def installed() -> dict | None:
    try:
        data = json.loads(VERSION_FILE.read_text())
    except (OSError, ValueError):
        return None
    return data if BINARY.is_file() else None


def newest_release(releases: list[dict], cpu: str) -> dict | None:
    """The most recently published MineNode PicoLimbo release that has a build for this CPU."""
    candidates = [r for r in releases
                  if str(r.get("tag_name", "")).startswith(TAG_PREFIX) and not r.get("draft")
                  and any(a.get("name") == asset_name(cpu) for a in r.get("assets", []))]
    candidates.sort(key=lambda r: r.get("published_at") or "", reverse=True)
    return candidates[0] if candidates else None


async def fetch_releases() -> list[dict]:
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return await net.get_json(f"https://api.github.com/repos/{REPO}/releases", params={"per_page": 50},
                              headers=headers)


async def _expected_sha256(release: dict, archive: dict) -> str:
    digest = archive.get("digest") or ""
    if digest.startswith("sha256:"):
        return digest.split(":", 1)[1].lower()
    checksum = next((a for a in release.get("assets", []) if a.get("name") == archive["name"] + ".sha256"), None)
    if not checksum:
        raise PicoLimboError("Wydanie PicoLimbo nie ma sumy kontrolnej – pomijam je")
    tmp = TMP_DIR / f"picolimbo-{time.time_ns()}.sha256"
    try:
        await net.download(checksum["browser_download_url"], tmp)
        value = tmp.read_text().split()[0].lower()
    finally:
        tmp.unlink(missing_ok=True)
    if len(value) != 64:
        raise PicoLimboError("Nieprawidłowa suma kontrolna PicoLimbo")
    return value


def _extract_binary(archive_path: Path, dest: Path) -> None:
    """Take only the pico_limbo executable out of the archive (never trust other paths in it)."""
    with tarfile.open(archive_path, "r:gz") as tar:
        member = next((m for m in tar.getmembers() if m.isfile() and Path(m.name).name == "pico_limbo"), None)
        if member is None:
            raise PicoLimboError("Archiwum PicoLimbo nie zawiera pliku pico_limbo")
        src = tar.extractfile(member)
        with open(dest, "wb") as out:
            shutil.copyfileobj(src, out)
    dest.chmod(0o755)


async def install(release: dict, cpu: str) -> dict:
    archive = next(a for a in release["assets"] if a.get("name") == asset_name(cpu))
    if (archive.get("size") or 0) > MAX_ARCHIVE:
        raise PicoLimboError("Archiwum PicoLimbo jest podejrzanie duże – pomijam je")
    sha256 = await _expected_sha256(release, archive)
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    tmp_archive = TMP_DIR / f"picolimbo-{time.time_ns()}.tar.gz"
    tmp_binary = BIN_DIR / ".pico_limbo.new"
    try:
        await net.download(archive["browser_download_url"], tmp_archive, sha256=sha256)
        _extract_binary(tmp_archive, tmp_binary)
        os.replace(tmp_binary, BINARY)
    finally:
        tmp_archive.unlink(missing_ok=True)
        tmp_binary.unlink(missing_ok=True)
    info = {"tag": release["tag_name"], "version": display_version(release["tag_name"]), "sha256": sha256,
            "installed_at": time.time(), "repo": REPO}
    VERSION_FILE.write_text(json.dumps(info))
    log.info("Installed PicoLimbo %s", release["tag_name"])
    return info


class Updater:
    """Keeps the lobby's PicoLimbo binary installed and current."""

    def __init__(self) -> None:
        self.checked_at: float | None = None
        self.latest: str | None = None
        self.error: str | None = None

    def status(self) -> dict:
        current = installed()
        return {
            "source": "local" if LOCAL_OVERRIDE else "github",
            "repo": REPO,
            "version": "lokalna binarka" if LOCAL_OVERRIDE else (current or {}).get("version"),
            "tag": (current or {}).get("tag"),
            "latest": display_version(self.latest),
            "checked_at": self.checked_at,
            "error": self.error,
        }

    async def binary(self) -> Path:
        """Path to run; installs the newest release first if nothing is installed yet."""
        if LOCAL_OVERRIDE:
            path = Path(LOCAL_OVERRIDE)
            if not path.is_file():
                raise PicoLimboError(f"MINENODE_PICOLIMBO_BINARY wskazuje na nieistniejący plik: {path}")
            return path
        if installed():
            return BINARY
        await self.update()
        if not installed():
            raise PicoLimboError(self.error or "Nie udało się pobrać PicoLimbo z GitHuba")
        return BINARY

    async def update(self) -> bool:
        """Install a newer release if there is one. Returns True when the binary changed."""
        if LOCAL_OVERRIDE:
            return False
        cpu = arch()
        try:
            if cpu is None:
                raise PicoLimboError(f"Brak PicoLimbo dla procesora {platform.machine()}")
            release = newest_release(await fetch_releases(), cpu)
            self.checked_at = time.time()
            if release is None:
                raise PicoLimboError(f"Repozytorium {REPO} nie ma jeszcze wydania PicoLimbo "
                                     "(uruchom workflow „PicoLimbo for MineNode” na GitHubie)")
            self.latest = release["tag_name"]
            current = installed()
            if current and current.get("tag") == release["tag_name"]:
                self.error = None
                return False
            await install(release, cpu)
            self.error = None
            return True
        except Exception as exc:  # noqa: BLE001 – the lobby keeps running the installed version
            self.error = f"Aktualizacja PicoLimbo: {exc}"
            log.warning("PicoLimbo update failed: %s", exc)
            return False
