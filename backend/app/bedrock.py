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
import zipfile
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING

from . import net
from . import bedrock_compat
from .config import BEDROCK_HOST, BEDROCK_PORT, BEDROCK_SAVED_LOGINS, DATA_DIR, PROXY_PORT, TMP_DIR
from .java import ensure_java
from .models import HOSTNAME_RE

if TYPE_CHECKING:
    from .manager import Manager, ServerInstance

log = logging.getLogger("minenode.bedrock")

DOWNLOAD_API = "https://download.geysermc.org/v2/projects"
GEYSER_META = f"{DOWNLOAD_API}/geyser/versions/latest/builds/latest"
BEDROCK_DIR = DATA_DIR / "bedrock"
GEYSER_JAR = BEDROCK_DIR / "geyser-standalone.jar"
CONFIG = BEDROCK_DIR / "config.yml"
EXTENSIONS_DIR = BEDROCK_DIR / "extensions"
ROUTER_JAR = EXTENSIONS_DIR / "MineNodeRouter.jar"
ROUTER_DIR = EXTENSIONS_DIR / "minenode-router"
ROUTES = ROUTER_DIR / "routes.properties"
BUNDLED_ROUTER_JAR = Path(__file__).resolve().parent.parent / "bedrock_ext" / "MineNodeRouter.jar"
ROUTER_VERSION = "1.0.6"

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


_resolve_cache: dict[str, tuple[float, bool]] = {}


def _resolves(host: str) -> bool:
    """Geyser refuses to start signaling when an advertised host name does not resolve – check first (cached)."""
    import socket
    now = time.monotonic()
    hit = _resolve_cache.get(host)
    if hit and now - hit[0] < 300:
        return hit[1]
    try:
        socket.getaddrinfo(host, None)
        ok = True
    except OSError:
        ok = False
    _resolve_cache[host] = (now, ok)
    return ok


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
        self._running_config: str | None = None  # config.yml text Geyser was started with
        self.geyser_java_version = bedrock_compat.GEYSER_JAVA_FALLBACK
        self._geyser_check_at = 0.0
        self._geyser_changed = False

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
            if inst.cfg.bedrock_enabled and self._bedrock_ready(inst) and inst.online_mode:
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
            "java_version": self.geyser_java_version,
            "router_version": ROUTER_VERSION,
            "auth": self._running_auth or self.auth_mode(),
            "servers": [i.cfg.id for i in self.m.instances.values() if i.cfg.bedrock_enabled],
            "routes": len(self._route_snapshot),
            "conflicts": self._conflicts,
            "advertise": self.advertise_addresses()[0],
            "advertise_source": self.advertise_addresses()[1],
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
        return {host: inst for host, inst in owners.items()
                if inst.cfg.bedrock_enabled and self._bedrock_ready(inst)}

    def _bedrock_ready(self, inst: "ServerInstance") -> bool:
        # Lightweight test doubles and old configurations may not have the
        # newer fields yet.  Real ServerConfig objects always do.
        if not hasattr(inst.cfg, "mc_version"):
            return True
        state = bedrock_compat.quick_state(inst, self.geyser_java_version)
        return state.get("supported", False) and not state.get("restart_required", False) and (
            state.get("addon_ready", False) or state.get("state") == "native")

    def advertise_addresses(self) -> tuple[list[str], str]:
        """Addresses Geyser sends to Bedrock players for the NetherNet (WebRTC) game connection.

        Left empty, Geyser falls back to this machine's own addresses (the container IP in Docker) plus a
        STUN-discovered public address. Behind Docker/home routers NAT usually remaps the UDP port, so STUN
        reports a random port that the router does not forward – players see the server but cannot join.
        We therefore advertise an explicit public address with the Bedrock port whenever we know one.
        Returns (addresses, source) where source is "settings", "public_address", "hostname" or "stun".
        """
        s = self.m.settings
        manual = [a.strip() for a in (getattr(s, "bedrock_advertise", "") or "").split(",") if a.strip()]
        if manual:
            return manual, "settings"
        public = normalize_host(s.public_address)
        if public and valid_host(public):
            return [f"[{public}]" if ":" in public else public], "public_address"
        # a server hostname under the base domain resolves through the wildcard record
        # prefer server subdomains: a wildcard record covers them, the bare base domain often has no record
        roots = {normalize_host(s.base_domain), public}
        for host in sorted(self.route_map(), key=lambda h: (h in roots, h)):
            if not _is_ip(host) and _resolves(host):
                return [host], "hostname"
        return [], "stun"

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
        # Hosts of servers with Bedrock turned off are rejected instead of reaching that server via the Java proxy.
        for index, host in enumerate(sorted(self.reserved_hosts() - set(routes))):
            lines.append(f"deny.{index}={host}")
        # Anything else (LAN IP, unknown name) goes to the default server – like the Java proxy – if it has Bedrock.
        default = self._default_server()
        if default is not None and default.cfg.bedrock_enabled and self._bedrock_ready(default):
            auth = "online" if default.online_mode else "offline"
            lines.append(f"fallback={default.cfg.id}|{auth}|127.0.0.1|{PROXY_PORT}")
        return "\n".join(lines) + "\n", routes

    def reserved_hosts(self) -> set[str]:
        """Every hostname any server owns (aliases and subdomains), Bedrock-enabled or not."""
        s = self.m.settings
        base, public = normalize_host(s.base_domain), normalize_host(s.public_address)
        root = base or (public if public and not _is_ip(public) else "")
        out: set[str] = set()
        for inst in self.m.instances.values():
            for alias in inst.cfg.aliases:
                h = normalize_host(alias)
                if valid_host(h):
                    out.add(h)
            if root:
                h = normalize_host(f"{inst.cfg.subdomain}.{root}")
                if valid_host(h):
                    out.add(h)
        return out

    # ------------------------------------------------------------ generated files
    def _config_text(self, auth_mode: str | None = None) -> str:
        auth_mode = auth_mode or self.auth_mode()
        saved_logins = "[" + ", ".join(_yaml(name) for name in BEDROCK_SAVED_LOGINS) + "]"
        advertise = "[" + ", ".join(_yaml(a) for a in self.advertise_addresses()[0]) + "]"
        return (
            f"# Generated by MineNode Bedrock router {ROUTER_VERSION}. Portable settings only: Geyser adds its own defaults on start.\n"
            "bedrock:\n"
            f"  address: {_yaml(self.host)}\n"
            f"  port: {self.port}\n"
            "  webrtc-port: 0\n"
            "  transport: nethernet\n"
            "  signaling:\n"
            "    mode: builtin\n"
            f"    advertise-addresses: {advertise}\n"
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
        if GEYSER_JAR.exists():
            self._read_geyser_java_version()
        await self._update_geyser()
        self._read_geyser_java_version()
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

    def _read_geyser_java_version(self) -> None:
        """Read the public codec version from Geyser without depending on internals."""
        try:
            with zipfile.ZipFile(GEYSER_JAR) as zf:
                raw = zf.read("org/geysermc/mcprotocollib/protocol/codec/MinecraftCodec.class")
                if self.geyser_version in ("", "installed"):
                    try:
                        props = zf.read("git.properties").decode("utf-8", "replace")
                        match = re.search(r"^git\.build\.version=(.+)$", props, re.MULTILINE)
                        build = re.search(r"^git\.build\.number=(.+)$", props, re.MULTILINE)
                        if match:
                            self.geyser_version = match.group(1).strip()
                            if build:
                                self.geyser_version += f"-b{build.group(1).strip()}"
                    except KeyError:
                        pass
            # MinecraftCodec stores the version as a normal CONSTANT_Utf8.
            versions = re.findall(rb"(?:1\.[0-9]+(?:\.[0-9]+)?|(?:2[0-9])\.[0-9]+)", raw)
            if versions:
                self.geyser_java_version = max((v.decode() for v in versions), key=bedrock_compat.version_key)
        except (OSError, KeyError, zipfile.BadZipFile):
            log.warning("Could not read Geyser supported Java version; using %s", self.geyser_java_version)

    async def _update_geyser(self) -> None:
        """Install the newest promoted Geyser build and atomically replace old JARs."""
        now = time.monotonic()
        if GEYSER_JAR.exists() and now - self._geyser_check_at < 3600:
            return
        self._geyser_check_at = now
        if not GEYSER_JAR.exists():
            # Keep the original download path for first installation and for
            # deployments/tests that provide a mocked Geyser downloader.
            await self._download("geyser", "standalone", GEYSER_JAR)
            self._geyser_changed = True
            return
        # A version learned from the JAR metadata is needed to compare builds.
        # If this is not a valid Geyser archive, leave it untouched and let the
        # normal process start report the actual problem.
        if not self.geyser_version:
            return
        meta = await net.get_json(GEYSER_META)
        item = meta.get("downloads", {}).get("standalone")
        if not item:
            raise RuntimeError("Geyser download 'standalone' is not available")
        wanted = f"{meta['version']}-b{meta['build']}"
        if GEYSER_JAR.exists() and self.geyser_version == wanted:
            return
        url = f"{DOWNLOAD_API}/geyser/versions/{meta['version']}/builds/{meta['build']}/downloads/standalone"
        tmp = TMP_DIR / "geyser-standalone.jar.download"
        await net.download(url, tmp, sha256=item.get("sha256"))
        os.replace(tmp, GEYSER_JAR)
        self.geyser_version = wanted
        self._geyser_changed = True
        self._append_log(f"Pobrano Geyser {wanted}")
        log.info("Installed Geyser %s", wanted)

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
            if self.running and self._geyser_changed:
                # The JAR cannot be replaced safely while the process is
                # using it.  Restart only the gateway, never Java servers.
                await self._stop_process()
                self._geyser_changed = False
            for inst in self.m.instances.values():
                if inst.cfg.bedrock_enabled and hasattr(inst.cfg, "mc_version"):
                    await bedrock_compat.ensure(inst, self.geyser_java_version)
            self._write_routes()

            desired_auth = self.auth_mode()
            desired_config = self._config_text(desired_auth)
            if self.running and self._running_config != desired_config:
                # Geyser reads config.yml (auth type, advertised addresses…) only at startup. A change
                # restarts Geyser alone; the Java proxy and every Minecraft server keep running.
                log.info("Bedrock gateway config changed (auth %s -> %s); restarting Geyser",
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
            if self.running:
                self._running_config = desired_config
                self._geyser_changed = False

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
            self._running_config = None
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
                low = text.lower()
                if "signaling started" in low:
                    self.listening = True
                elif "signaling will not start" in low or "signaling could not start" in low:
                    self.error = f"Signaling Bedrock nie wystartował: {text[:400]}"
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
                self._running_config = None
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
        self._running_config = None
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
