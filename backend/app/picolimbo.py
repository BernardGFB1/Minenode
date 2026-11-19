"""PicoLimbo for the authentication lobby: built on this server from the author's source code.

Everything comes from the author's repository (https://github.com/Quozul/PicoLimbo): the panel asks
GitHub for the newest release, downloads that release's source archive, applies MineNode's login
module (``assets/picolimbo/minenode-auth.patch`` – our own code, not a copy of PicoLimbo) and
compiles it here with the Rust toolchain PicoLimbo pins in ``rust-toolchain.toml``. MineNode
publishes no PicoLimbo binaries of its own.

The Rust toolchain is installed once with the official rustup into ``/data/auth-lobby/rust``.
Compiling takes a few minutes; it runs in the background at a low priority while the lobby keeps
running the previous build. A newer upstream release (checked every few hours) or a changed login
module triggers a new build. ``MINENODE_PICOLIMBO_BINARY`` points at a local binary instead.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import platform
import re
import shutil
import tarfile
import time
from pathlib import Path
from urllib.parse import quote

from . import net
from .config import AUTH_LOBBY_DIR, TMP_DIR

log = logging.getLogger("minenode.picolimbo")

REPO = os.environ.get("MINENODE_PICOLIMBO_REPO", "Quozul/PicoLimbo")  # the author's repository
CHECK_INTERVAL = 6 * 3600
FAILED_BUILD_RETRY = 6 * 3600   # a build that failed is not retried automatically more often than this
BUILD_TIMEOUT = 90 * 60
MAX_SOURCE = 300 * 1024 * 1024
PATCH = Path(__file__).parent / "assets" / "picolimbo" / "minenode-auth.patch"
# Crates the login module uses; added with `cargo add` so upstream Cargo.toml changes never break the patch.
EXTRA_CRATES = ("hex@0.4", "getrandom@0.3")
RECIPE = "1"  # bump when the build steps change in a way that needs a rebuild

BIN_DIR = AUTH_LOBBY_DIR / "bin"
BINARY = BIN_DIR / "pico_limbo"
VERSION_FILE = BIN_DIR / "version.json"
BUILD_DIR = AUTH_LOBBY_DIR / "build"
RUST_DIR = AUTH_LOBBY_DIR / "rust"   # RUSTUP_HOME and CARGO_HOME
LOCAL_OVERRIDE = os.environ.get("MINENODE_PICOLIMBO_BINARY")

RUSTUP_DIST = "https://static.rust-lang.org/rustup/dist"


class PicoLimboError(Exception):
    pass


def host_triple() -> str | None:
    machine = platform.machine().lower()
    cpu = {"x86_64": "x86_64", "amd64": "x86_64", "aarch64": "aarch64", "arm64": "aarch64"}.get(machine)
    if not cpu:
        return None
    system = platform.system()
    if system == "Linux":
        return f"{cpu}-unknown-linux-gnu"
    if system == "Darwin":
        return f"{cpu}-apple-darwin"
    return None


def patch_id() -> str:
    """Identifies the login module and build recipe: a change rebuilds PicoLimbo."""
    h = hashlib.sha256(PATCH.read_bytes())
    h.update(("|".join(EXTRA_CRATES) + "|" + RECIPE).encode())
    return h.hexdigest()[:8]


def display_version(tag: str | None) -> str | None:
    """v1.15.0+mc26.3 -> 1.15.0+mc26.3"""
    if not tag:
        return None
    return tag[1:] if tag.startswith("v") else tag


def installed() -> dict | None:
    """The installed build, only if it was compiled here from the author's source."""
    try:
        data = json.loads(VERSION_FILE.read_text())
    except (OSError, ValueError):
        return None
    return data if BINARY.is_file() else None


def is_own_build(info: dict | None) -> bool:
    return bool(info and info.get("source") == "build" and info.get("repo") == REPO)


# ====================================================================== upstream release

async def latest_tag() -> str:
    """Tag of the author's newest release (API first, the release page redirect when rate limited)."""
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        data = await net.get_json(f"https://api.github.com/repos/{REPO}/releases/latest", headers=headers)
        tag = str(data.get("tag_name") or "")
    except Exception as exc:  # noqa: BLE001 – e.g. the unauthenticated API limit of 60 requests per hour
        log.info("GitHub API unavailable (%s) – reading the release page", exc)
        r = await net.client().head(f"https://github.com/{REPO}/releases/latest", follow_redirects=False)
        location = r.headers.get("location", "")
        m = re.search(r"/releases/tag/([^/?#]+)$", location)
        tag = m.group(1) if m else ""
    if not re.fullmatch(r"[A-Za-z0-9._+\-]{1,80}", tag):
        raise PicoLimboError(f"Nie udało się odczytać najnowszego wydania {REPO}")
    return tag


def source_url(tag: str) -> str:
    return f"https://github.com/{REPO}/archive/refs/tags/{quote(tag, safe='')}.tar.gz"


def _extract_source(archive: Path, dest: Path) -> Path:
    """Unpack the source archive into ``dest`` (regular files and folders only, nothing outside it)."""
    dest.mkdir(parents=True, exist_ok=True)
    root = dest.resolve()
    with tarfile.open(archive, "r:gz") as tar:
        for m in tar.getmembers():
            parts = Path(m.name).parts[1:]  # GitHub puts everything under "<repo>-<version>/"
            if not parts or m.name.startswith("/") or ".." in parts:
                continue
            target = (root / Path(*parts)).resolve()
            if not target.is_relative_to(root):
                continue
            if m.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif m.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                with tar.extractfile(m) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out)
                if m.mode & 0o111:
                    target.chmod(0o755)
            # links and special files are skipped – the Rust sources need none
    if not (root / "Cargo.toml").is_file() or not (root / "pico_limbo").is_dir():
        raise PicoLimboError("Archiwum nie wygląda na źródła PicoLimbo")
    return root


# ====================================================================== build

class Build:
    """One compile run; ``stage`` and the log tail are shown in the panel."""

    def __init__(self) -> None:
        self.stage = "Przygotowanie"
        self.started_at = time.time()
        self.tail: list[str] = []
        self.log_path = BUILD_DIR / "build.log"

    def note(self, text: str) -> None:
        self.tail = (self.tail + [text])[-40:]
        try:
            with open(self.log_path, "a", encoding="utf-8") as fh:
                fh.write(text + "\n")
        except OSError:
            pass

    def env(self) -> dict:
        env = dict(os.environ)
        rust = RUST_DIR.absolute()
        env.update({
            "RUSTUP_HOME": str(rust), "CARGO_HOME": str(rust),
            "PATH": os.pathsep.join([str(rust / "bin"), env.get("PATH", "")]),
            "CARGO_TARGET_DIR": str((BUILD_DIR / "target").absolute()),
            # Half of the cores at most: the game servers keep running during the build.
            "CARGO_BUILD_JOBS": str(max(1, (os.cpu_count() or 2) // 2)),
            "CARGO_TERM_COLOR": "never", "CARGO_TERM_PROGRESS_WHEN": "never",
        })
        env.pop("RUSTUP_TOOLCHAIN", None)
        return env

    async def run(self, *cmd: str, cwd: Path | None = None) -> None:
        self.note("$ " + " ".join(cmd))
        nice = ["nice", "-n", "15"] if shutil.which("nice") else []
        proc = await asyncio.create_subprocess_exec(*nice, *cmd, cwd=str(cwd) if cwd else None, env=self.env(),
                                                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        try:
            async def pump():
                async for raw in proc.stdout:
                    line = raw.decode("utf-8", "replace").rstrip()
                    if line:
                        self.note(line)
            await asyncio.wait_for(asyncio.gather(pump(), proc.wait()), BUILD_TIMEOUT)
        except asyncio.TimeoutError:
            proc.kill()
            raise PicoLimboError("Kompilacja PicoLimbo trwała zbyt długo")
        except asyncio.CancelledError:
            proc.kill()
            raise
        if proc.returncode != 0:
            raise PicoLimboError(f"Polecenie {Path(cmd[0]).name} zakończyło się błędem ({proc.returncode})")


def missing_tools() -> list[str]:
    missing = [] if (shutil.which("cc") or shutil.which("gcc")) else ["kompilator C (gcc)"]
    if not shutil.which("patch"):
        missing.append("patch")
    return missing


async def _ensure_rustup(build: Build, triple: str) -> None:
    if (RUST_DIR / "bin" / "rustup").is_file():
        return
    build.stage = "Instalacja Rusta (rustup)"
    RUST_DIR.mkdir(parents=True, exist_ok=True)
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    url = f"{RUSTUP_DIST}/{triple}/rustup-init"
    sums = TMP_DIR / f"rustup-{time.time_ns()}.sha256"
    init = TMP_DIR / f"rustup-init-{time.time_ns()}"
    try:
        await net.download(url + ".sha256", sums)
        expected = sums.read_text().split()[0].lower()
        if len(expected) != 64:
            raise PicoLimboError("Nieprawidłowa suma kontrolna rustup")
        await net.download(url, init, sha256=expected)
        init.chmod(0o755)
        # The toolchain itself comes from PicoLimbo's rust-toolchain.toml when cargo first runs.
        await build.run(str(init), "-y", "--no-modify-path", "--profile", "minimal", "--default-toolchain", "none")
    finally:
        sums.unlink(missing_ok=True)
        init.unlink(missing_ok=True)


async def compile_release(tag: str, build: Build) -> dict:
    """Download the author's source for ``tag``, add the login module and compile it."""
    triple = host_triple()
    if not triple:
        raise PicoLimboError(f"Kompilacja PicoLimbo nie jest obsługiwana na {platform.system()} {platform.machine()}")
    missing = missing_tools()
    if missing:
        raise PicoLimboError("Do kompilacji PicoLimbo brakuje: " + ", ".join(missing)
                             + " (obraz Docker MineNode je zawiera – zaktualizuj obraz)")
    shutil.rmtree(BUILD_DIR, ignore_errors=True)
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    pid = patch_id()
    build.note(f"PicoLimbo {tag} z {REPO} + moduł logowania MineNode ({pid}), {triple}")
    await _ensure_rustup(build, triple)

    build.stage = "Pobieranie źródeł PicoLimbo"
    archive = BUILD_DIR / "source.tar.gz"
    await net.download(source_url(tag), archive)
    if archive.stat().st_size > MAX_SOURCE:
        raise PicoLimboError("Archiwum źródeł PicoLimbo jest podejrzanie duże")
    src = await asyncio.to_thread(_extract_source, archive, BUILD_DIR / "src")
    archive.unlink(missing_ok=True)

    build.stage = "Dodawanie modułu logowania"
    await build.run("patch", "-p1", "--forward", "--batch", "-i", str(PATCH.resolve()), cwd=src)
    # absolute(), not resolve(): rustup's cargo is a link to rustup and must keep its own name
    cargo = str((RUST_DIR / "bin" / "cargo").absolute())
    await build.run(cargo, "add", "--quiet", "--package", "pico_limbo", *EXTRA_CRATES, cwd=src)

    build.stage = "Kompilacja (kilka minut)"
    await build.run(cargo, "build", "--release", "--bin", "pico_limbo", cwd=src)
    built = BUILD_DIR / "target" / "release" / "pico_limbo"
    if not built.is_file():
        raise PicoLimboError("Kompilacja nie utworzyła pliku pico_limbo")

    BIN_DIR.mkdir(parents=True, exist_ok=True)
    tmp = BIN_DIR / ".pico_limbo.new"
    shutil.copyfile(built, tmp)
    tmp.chmod(0o755)
    os.replace(tmp, BINARY)  # a running lobby keeps its open copy until it restarts
    digest = hashlib.sha256(BINARY.read_bytes()).hexdigest()
    info = {"tag": tag, "version": display_version(tag), "patch": pid, "repo": REPO, "source": "build",
            "sha256": digest, "built_at": time.time(), "build_seconds": round(time.time() - build.started_at)}
    VERSION_FILE.write_text(json.dumps(info))
    # Sources and object files are large; the toolchain and crate cache stay for the next build.
    await asyncio.to_thread(shutil.rmtree, BUILD_DIR / "src", True)
    await asyncio.to_thread(shutil.rmtree, BUILD_DIR / "target", True)
    log.info("Built PicoLimbo %s (%s) in %ss", tag, pid, info["build_seconds"])
    return info


class Updater:
    """Keeps the lobby's PicoLimbo built from the newest release of the author's repository."""

    def __init__(self) -> None:
        self.checked_at: float | None = None
        self.latest: str | None = None
        self.error: str | None = None
        self.build: Build | None = None
        self._lock = asyncio.Lock()
        self._failed: tuple[str, str, float] | None = None  # (tag, patch id, when)

    @property
    def building(self) -> bool:
        return self.build is not None

    def status(self) -> dict:
        current = installed()
        own = is_own_build(current)
        b = self.build
        return {
            "source": "local" if LOCAL_OVERRIDE else "build",
            "repo": REPO,
            "version": "lokalna binarka" if LOCAL_OVERRIDE else (current or {}).get("version") if own else None,
            "tag": (current or {}).get("tag") if own else None,
            "legacy": bool(current and not own and not LOCAL_OVERRIDE),  # an old binary from MineNode releases
            "built_at": (current or {}).get("built_at") if own else None,
            "latest": display_version(self.latest),
            "checked_at": self.checked_at,
            "error": self.error,
            "building": bool(b),
            "stage": b.stage if b else None,
            "build_started_at": b.started_at if b else None,
            "build_log": b.tail[-12:] if b else None,
        }

    def ready_binary(self) -> Path | None:
        """A binary that can run right now (an older build keeps the lobby working during a rebuild)."""
        if LOCAL_OVERRIDE:
            path = Path(LOCAL_OVERRIDE)
            return path if path.is_file() else None
        return BINARY if installed() else None

    async def binary(self) -> Path:
        if LOCAL_OVERRIDE:
            path = Path(LOCAL_OVERRIDE)
            if not path.is_file():
                raise PicoLimboError(f"MINENODE_PICOLIMBO_BINARY wskazuje na nieistniejący plik: {path}")
            return path
        path = self.ready_binary()
        if path:
            return path
        await self.update(force=True)
        if not installed():
            raise PicoLimboError(self.error or "Nie udało się skompilować PicoLimbo")
        return BINARY

    def needs_build(self) -> bool:
        return not LOCAL_OVERRIDE and not is_own_build(installed())

    async def update(self, force: bool = False) -> bool:
        """Build the newest upstream release if it (or the login module) changed. True when rebuilt."""
        if LOCAL_OVERRIDE:
            return False
        async with self._lock:
            compiling = False
            tail: list[str] = []
            try:
                tag = await latest_tag()
                self.checked_at = time.time()
                self.latest = tag
                pid = patch_id()
                current = installed()
                if is_own_build(current) and current.get("tag") == tag and current.get("patch") == pid:
                    self.error = None
                    return False
                if (not force and self._failed and self._failed[:2] == (tag, pid)
                        and time.time() - self._failed[2] < FAILED_BUILD_RETRY):
                    return False  # do not burn CPU on the same failing build every few minutes
                self.build = Build()
                compiling = True
                try:
                    await compile_release(tag, self.build)
                finally:
                    tail = self.build.tail
                    self.build = None
                self._failed = None
                self.error = None
                return True
            except Exception as exc:  # noqa: BLE001 – the lobby keeps running the installed build
                if compiling:  # a network hiccup before the build is retried at the next check
                    self._failed = (self.latest, patch_id(), time.time())
                detail = ""
                if tail:
                    detail = " – " + " | ".join(line for line in tail[-4:] if line)[-400:]
                self.error = f"PicoLimbo: {exc}{detail}"
                log.warning("PicoLimbo build failed: %s", exc)
                return False
