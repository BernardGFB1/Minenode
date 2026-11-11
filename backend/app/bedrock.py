"""Shared Bedrock Edition gateway.

A single Geyser Standalone process serves every Bedrock-enabled MineNode server through one public
port.  Geyser forwards each connection to the existing Java proxy on ``127.0.0.1:PROXY_PORT`` and
forwards the hostname the Bedrock client typed, so Java routing, whitelist attempts, start-on-join
and voice binding keep working exactly as before.  The Java proxy itself is not modified.

Per-server routing and online/offline authentication live in the bundled ``MineNodeRouter`` Geyser
extension.  It reads ``routes.properties`` generated here and reloads it when the file changes.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import ipaddress
import logging
import os
import re
import shutil
import time
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING

from . import net
from .config import BEDROCK_HOST, BEDROCK_PORT, BEDROCK_SAVED_LOGINS, DATA_DIR, PROXY_PORT, TMP_DIR
from .java import ensure_java
from .models import HOSTNAME_RE

if TYPE_CHECKING:
    from .manager import Manager, ServerInstance

log = logging.getLogger("minenode.bedrock")

DOWNLOAD_API = "https://download.geysermc.org/v2/projects"
BEDROCK_DIR = DATA_DIR / "bedrock"
GEYSER_JAR = BEDROCK_DIR / "geyser-standalone.jar"
CONFIG = BEDROCK_DIR / "config.yml"
EXTENSIONS_DIR = BEDROCK_DIR / "extensions"
ROUTER_JAR = EXTENSIONS_DIR / "MineNodeRouter.jar"
ROUTER_DIR = EXTENSIONS_DIR / "minenode-router"
ROUTES = ROUTER_DIR / "routes.properties"
BUNDLED_ROUTER_JAR = Path(__file__).resolve().parent.parent / "bedrock_ext" / "MineNodeRouter.jar"

DENIED_MESSAGE = ("Ten adres nie jest dostępny przez Bedrock Edition. "
                  "Włącz obsługę Bedrock dla serwera w panelu MineNode.")
RESTART_DELAY = 60.0
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def normalize_host(raw: str | None) -> str:
    """Return a bare, lowercase hostname (Bedrock clients sometimes include a port or scheme)."""
    host = (raw or "").strip().lower()
    if "://" in host:
        host = host.split("://", 1)[1]
    host = host.split("/", 1)[0].strip()
    if host.startswith("["):
        end = host.find("]")
        return host[1:end] if end > 1 else host
    if host.count(":") == 1:
        host = host.split(":", 1)[0]
    return host.rstrip(".")


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def valid_host(host: str) -> bool:
    if not host:
        return False
    if _is_ip(host):
        return True
    return bool(HOSTNAME_RE.match(host))


def _yaml(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _escape_property(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\r", "\\r").replace("\n", "\\n")


def _write_if_changed(path: Path, text: str) -> bool:
    """Atomically replace ``path`` when its content differs. Returns True when it was written."""
    try:
        if path.exists() and path.read_text(encoding="utf-8") == text:
            return False
    except OSError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)
    return True


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class BedrockProxy:
    """Lifecycle manager for the one shared Geyser instance."""

    def __init__(self, manager: "Manager", host: str = BEDROCK_HOST, port: int = BEDROCK_PORT):
        self.m = manager
        self.host, self.port = host, port
        self.proc: asyncio.subprocess.Process | None = None
        self.error: str | None = None
        self.listening = False
        self.log_tail: deque[str] = deque(maxlen=80)
        self._log_task: asyncio.Task | None = None
        self._watch_task: asyncio.Task | None = None
        self._apply_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._dirty = asyncio.Event()
        self._stopping = False
        self._next_start = 0.0
        self._router_synced = False
        self._running_auth: str | None = None
        self.geyser_version = ""
        self._route_snapshot: dict[str, "ServerInstance"] = {}
        self._conflicts: list[str] = []

    # ------------------------------------------------------------ state
    def enabled(self) -> bool:
        return any(i.cfg.bedrock_enabled for i in self.m.instances.values())

    def auth_mode(self) -> str:
        """Geyser's global authentication mode.

        ``online`` is required as soon as one Bedrock-enabled Java server runs in online mode:
        Geyser then authenticates players with their Xbox-linked Java account. When every
        Bedrock-enabled server is offline-mode, ``offline`` removes the Java Edition ownership
        requirement for Bedrock players.
        """
        for inst in self.m.instances.values():
            if inst.cfg.bedrock_enabled and inst.online_mode:
                return "online"
        return "offline"

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.returncode is None

    def stats(self) -> dict:
        return {
            "host": self.host,
            "port": self.port,
            "enabled": self.enabled(),
            "running": self.running,
            "listening": self.listening,
            "error": self.error,
            "version": self.geyser_version,
            "auth": self._running_auth or self.auth_mode(),
            "servers": [i.cfg.id for i in self.m.instances.values() if i.cfg.bedrock_enabled],
            "routes": len(self._route_snapshot),
            "conflicts": self._conflicts,
            "log": list(self.log_tail)[-8:] if self.error else [],
        }

    # ------------------------------------------------------------ routing table
    def _default_server(self) -> "ServerInstance | None":
        sid = self.m.settings.default_server_id
        inst = self.m.instances.get(sid) if sid else None
        if inst is not None:
            return inst
        enabled = [i for i in self.m.instances.values() if i.cfg.bedrock_enabled]
        return enabled[0] if len(enabled) == 1 else None

    def route_map(self) -> dict[str, "ServerInstance"]:
        """Build the hostname -> server map, mirroring the Java proxy's precedence.

        Aliases win, then ``subdomain.base_domain`` (or ``subdomain.public_address`` when no base
        domain is configured), then the bare base domain / public address for the default server.

        Disabled servers still reserve their hostnames: a host that belongs to a server with
        Bedrock disabled is rejected instead of being silently reassigned to another server.
        """
        instances = list(self.m.instances.values())
        if not any(i.cfg.bedrock_enabled for i in instances):
            return {}

        settings = self.m.settings
        base = normalize_host(settings.base_domain)
        public = normalize_host(settings.public_address)
        pairs: list[tuple[str, "ServerInstance"]] = []

        def add(host: str, inst: "ServerInstance") -> None:
            host = normalize_host(host)
            if valid_host(host):
                pairs.append((host, inst))

        # Same precedence as Manager.resolve_host: explicit aliases first.
        for inst in instances:
            for alias in inst.cfg.aliases:
                add(alias, inst)
        if base:
            for inst in instances:
                add(f"{inst.cfg.subdomain}.{base}", inst)
        elif public and not _is_ip(public):
            for inst in instances:
                add(f"{inst.cfg.subdomain}.{public}", inst)

        default = self._default_server()
        if default is not None:
            for root in (base, public):
                if root:
                    add(root, default)

        owners: dict[str, "ServerInstance"] = {}
        conflicts: list[str] = []
        for host, inst in pairs:
            existing = owners.get(host)
            if existing is None:
                owners[host] = inst
            elif existing is not inst:
                conflicts.append(f"{host}: {existing.cfg.name} / {inst.cfg.name}")
        self._conflicts = conflicts
        return {host: inst for host, inst in owners.items() if inst.cfg.bedrock_enabled}

    def hosts_for(self, inst: "ServerInstance") -> list[str]:
        return [host for host, routed in self.route_map().items() if routed is inst]

    def public_host(self, inst: "ServerInstance") -> str | None:
        hosts = self.hosts_for(inst)
        return hosts[0] if hosts else None

    def _routes_text(self) -> tuple[str, dict[str, "ServerInstance"]]:
        routes = self.route_map()
        lines = [
            "# Generated by MineNode – do not edit while the Bedrock gateway is enabled.",
            f"denied-message={_escape_property(DENIED_MESSAGE)}",
        ]
        for index, (host, inst) in enumerate(routes.items()):
            auth = "online" if inst.online_mode else "offline"
            lines.append(f"route.{index}={host}|{inst.cfg.id}|{auth}|127.0.0.1|{PROXY_PORT}")
        return "\n".join(lines) + "\n", routes

    # ------------------------------------------------------------ generated files
    def _config_text(self, auth_mode: str | None = None) -> str:
        auth_mode = auth_mode or self.auth_mode()
        saved_logins = "[" + ", ".join(_yaml(name) for name in BEDROCK_SAVED_LOGINS) + "]"
        return (
            "# Generated by MineNode. Portable settings only: Geyser adds its own defaults on start.\n"
            "bedrock:\n"
            f"  address: {_yaml(self.host)}\n"
            f"  port: {self.port}\n"
            "  webrtc-port: 0\n"
            "  transport: nethernet\n"
            "  signaling:\n"
            "    mode: builtin\n"
            "    advertise-addresses: []\n"
            "    stun-discovery: true\n"
            "java:\n"
            "  address: '127.0.0.1'\n"
            f"  port: {PROXY_PORT}\n"
            f"  auth-type: {auth_mode}\n"
            "  forward-hostname: true\n"
            f"saved-user-logins: {saved_logins}\n"
            "motd:\n"
            "  primary-motd: 'MineNode'\n"
            "  secondary-motd: 'Wybierz serwer – połącz się swoim adresem'\n"
            "  passthrough-motd: true\n"
            "  passthrough-player-counts: true\n"
            "gameplay:\n"
            "  server-name: 'MineNode'\n"
            "log-player-ip-addresses: false\n"
            "enable-metrics: false\n"
            "debug-mode: false\n"
        )

    def _write_config(self, auth_mode: str) -> None:
        _write_if_changed(CONFIG, self._config_text(auth_mode))

    def _write_routes(self) -> dict[str, "ServerInstance"]:
        text, routes = self._routes_text()
        _write_if_changed(ROUTES, text)
        self._route_snapshot = routes
        return routes

    def _clear_routes(self) -> None:
        _write_if_changed(
            ROUTES,
            "# No Bedrock servers are enabled.\n"
            f"denied-message={_escape_property(DENIED_MESSAGE)}\n",
        )
        self._route_snapshot = {}
        self._conflicts = []

    async def _ensure_files(self) -> None:
        BEDROCK_DIR.mkdir(parents=True, exist_ok=True)
        if not GEYSER_JAR.exists():
            await self._download("geyser", "standalone", GEYSER_JAR)
        if not self._router_synced:
            if not BUNDLED_ROUTER_JAR.is_file():
                raise RuntimeError(
                    "Brak dołączonego MineNodeRouter.jar – przebuduj backend/bedrock_ext"
                )
            EXTENSIONS_DIR.mkdir(parents=True, exist_ok=True)
            ROUTER_DIR.mkdir(parents=True, exist_ok=True)
            if not ROUTER_JAR.exists() or _sha256(ROUTER_JAR) != _sha256(BUNDLED_ROUTER_JAR):
                tmp = ROUTER_JAR.with_name(ROUTER_JAR.name + ".tmp")
                shutil.copyfile(BUNDLED_ROUTER_JAR, tmp)
                os.replace(tmp, ROUTER_JAR)
            self._router_synced = True

    async def _download(self, project: str, name: str, destination: Path) -> None:
        meta = await net.get_json(f"{DOWNLOAD_API}/{project}/versions/latest/builds/latest")
        version, build = meta["version"], meta["build"]
        item = meta.get("downloads", {}).get(name)
        if not item:
            raise RuntimeError(f"Geyser download '{name}' is not available")
        url = f"{DOWNLOAD_API}/{project}/versions/{version}/builds/{build}/downloads/{name}"
        tmp = TMP_DIR / f"{destination.name}.download"
        await net.download(url, tmp, sha256=item.get("sha256"))
        destination.parent.mkdir(parents=True, exist_ok=True)
        tmp.replace(destination)
        self.geyser_version = f"{version}-b{build}"
        self._append_log(f"Pobrano Geyser {self.geyser_version}")
        log.info("Downloaded %s %s build %s", project, version, build)

    # ------------------------------------------------------------ lifecycle
    def launch(self) -> None:
        """Begin watching configuration and start the gateway if needed (non-blocking)."""
        if self._watch_task is None or self._watch_task.done():
            self._watch_task = asyncio.create_task(self._watch(), name="bedrock-watch")
        self.request_apply()

    def request_apply(self) -> None:
        """Re-generate routes and start/stop Geyser without blocking the API request."""
        self._next_start = 0.0
        self._dirty.set()
        if self._apply_task is None or self._apply_task.done():
            self._apply_task = asyncio.create_task(self._apply_loop(), name="bedrock-apply")

    async def _apply_loop(self) -> None:
        while self._dirty.is_set():
            self._dirty.clear()
            try:
                await self.apply()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self.error = f"Błąd konfiguracji Bedrock: {exc}"
                log.exception("Bedrock gateway sync failed")

    async def _watch(self) -> None:
        while True:
            try:
                await asyncio.sleep(5)
                await self.apply()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self.error = f"Błąd konfiguracji Bedrock: {exc}"
                log.exception("Bedrock gateway watch failed")

    async def apply(self) -> None:
        """Synchronize generated files and the Geyser process with current panel state."""
        async with self._lock:
            if not self.enabled():
                self._clear_routes()
                await self._stop_process()
                self.error = None
                return

            await self._ensure_files()
            self._write_routes()

            desired_auth = self.auth_mode()
            if self.running and self._running_auth != desired_auth:
                # Geyser reads java.auth-type only at startup. Changing it restarts Geyser
                # alone; the Java proxy and every Minecraft server keep running.
                log.info("Bedrock auth mode changed %s -> %s; restarting Geyser",
                         self._running_auth, desired_auth)
                await self._stop_process()

            if self.running:
                # A successful sync clears errors left by a transient route/JAR problem.
                self.error = None
                return
            if time.monotonic() < self._next_start:
                return
            self._write_config(desired_auth)
            await self._start_process(desired_auth)

    async def _start_process(self, auth_mode: str) -> None:
        try:
            _, java = await ensure_java(21, self._append_log)
            self._stopping = False
            self.log_tail.clear()
            self.proc = await asyncio.create_subprocess_exec(
                str(java), "-jar", str(GEYSER_JAR), "--nogui",
                cwd=str(BEDROCK_DIR),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                limit=1024 * 1024,
                start_new_session=True,
            )
            self.error = None
            self.listening = False
            self._running_auth = auth_mode
            self._next_start = 0.0
            self._log_task = asyncio.create_task(self._read_log(self.proc), name="bedrock-log")
            log.info("Bedrock gateway starting Geyser on tcp/udp %s:%s (auth: %s)",
                     self.host, self.port, auth_mode)
        except Exception as exc:  # noqa: BLE001
            self.error = f"Nie udało się uruchomić Geysera: {exc}"
            self._running_auth = None
            self._next_start = time.monotonic() + RESTART_DELAY
            log.exception("Bedrock gateway failed to start")

    async def _read_log(self, proc: asyncio.subprocess.Process) -> None:
        assert proc.stdout is not None
        try:
            async for raw in proc.stdout:
                text = ANSI_RE.sub("", raw.decode("utf-8", "replace")).strip()
                if not text:
                    continue
                self._append_log(text)
                if "signaling started" in text.lower():
                    self.listening = True
                log.info("[Geyser] %s", text)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("Reading Geyser output failed")
        finally:
            if self.proc is proc:
                code = proc.returncode
                if code is None and not self._stopping:
                    with contextlib.suppress(Exception):
                        code = await proc.wait()
                self.proc = None
                self.listening = False
                self._running_auth = None
                self._log_task = None
                if not self._stopping:
                    tail = next(
                        (line for line in reversed(self.log_tail)
                         if "error" in line.lower() or "exception" in line.lower()),
                        self.log_tail[-1] if self.log_tail else "",
                    )
                    self.error = f"Geyser zakończył pracę (kod {code})"
                    if tail:
                        self.error += f": {tail[:400]}"
                    self._next_start = time.monotonic() + RESTART_DELAY
                    log.error(self.error)

    async def _cancel_log_task(self) -> None:
        task, self._log_task = self._log_task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _stop_process(self) -> None:
        proc, self.proc = self.proc, None
        self.listening = False
        self._running_auth = None
        if proc is None:
            await self._cancel_log_task()
            return
        self._stopping = True
        try:
            if proc.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), 10)
                except asyncio.TimeoutError:
                    with contextlib.suppress(ProcessLookupError):
                        proc.kill()
                    await proc.wait()
        finally:
            self._stopping = False
            await self._cancel_log_task()

    async def close(self) -> None:
        for task in (self._watch_task, self._apply_task):
            if task is not None:
                task.cancel()
        for task in (self._watch_task, self._apply_task):
            if task is not None:
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        self._watch_task = self._apply_task = None
        await self._stop_process()

    # ------------------------------------------------------------ log helpers
    def _append_log(self, text: str) -> None:
        text = ANSI_RE.sub("", str(text)).strip()
        if text:
            self.log_tail.append(text)
