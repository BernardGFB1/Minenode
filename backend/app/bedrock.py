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
GEYSER_STABLE = ("geyser", "latest")
GEYSER_PREVIEW_PROJECT = "geyserpreview"
GEYSER_PREVIEW_RE = re.compile(r"^pr\.[0-9]{1,6}$")
GEYSER_CHECK_INTERVAL = 3600.0
BEDROCK_DIR = DATA_DIR / "bedrock"
GEYSER_JAR = BEDROCK_DIR / "geyser-standalone.jar"
GEYSER_INSTALLED = BEDROCK_DIR / "geyser-standalone.installed"  # "<project> <version>-b<build>"
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
LOG_BUFFER = 800
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")



def _domains(settings) -> list[str]:
    """Main domains of the panel (the default one first); test doubles may only have ``base_domain``."""
    if hasattr(settings, "domains"):
        return [normalize_host(d) for d in settings.domains()]
    base = normalize_host(getattr(settings, "base_domain", ""))
    return [base] if base else []


def _domain_hosts(inst, domains: list[str]) -> list[str]:
    chosen = normalize_host(getattr(inst.cfg, "domain", None) or "")
    ordered = ([chosen] if chosen in domains else []) + [d for d in domains if d != chosen]
    return [f"{inst.cfg.subdomain}.{d}" for d in ordered]

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


_resolve_cache: dict[str, tuple[float, frozenset[str]]] = {}


def resolve_ips(host: str) -> frozenset[str]:
    """IP addresses a host name resolves to (cached for 5 minutes); empty when it does not resolve."""
    if _is_ip(host):
        return frozenset({str(ipaddress.ip_address(host))})
    import socket
    now = time.monotonic()
    hit = _resolve_cache.get(host)
    if hit and now - hit[0] < 300:
        return hit[1]
    try:
        ips = frozenset(str(ipaddress.ip_address(info[4][0].split("%", 1)[0])) for info in socket.getaddrinfo(host, None))
    except (OSError, ValueError):
        ips = frozenset()
    _resolve_cache[host] = (now, ips)
    return ips


def _resolves(host: str) -> bool:
    """Geyser refuses to start signaling when an advertised host name does not resolve – check first (cached)."""
    return bool(resolve_ips(host))


def _yaml(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _escape_property(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\r", "\\r").replace("\n", "\\n")


def _read_marker() -> tuple[str, str] | None:
    """(project, version) of the installed Geyser JAR, or None when unknown."""
    try:
        project, _, version = GEYSER_INSTALLED.read_text(encoding="utf-8").strip().partition(" ")
    except OSError:
        return None
    return (project, version) if project and version and GEYSER_JAR.exists() else None


def _write_marker(project: str, version: str) -> None:
    tmp = GEYSER_INSTALLED.with_name(GEYSER_INSTALLED.name + ".tmp")
    tmp.write_text(f"{project} {version}\n", encoding="utf-8")
    os.replace(tmp, GEYSER_INSTALLED)


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
        self.log_entries: deque[dict] = deque(maxlen=LOG_BUFFER)
        self._log_seq = 0
        self._log_epoch = 0
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
        self._geyser_source_checked: tuple[str, str] | None = None
        self._geyser_changed = False
        self.geyser_project = ""
        marker = _read_marker()
        if marker:
            self.geyser_project, self.geyser_version = marker

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

        Aliases win, then ``subdomain.<main domain>`` for every main domain of the panel (the one the
        server shows first; ``subdomain.public_address`` when no domain is configured), then the bare
        main domains / public address for the default server.

        Disabled servers still reserve their hostnames: a host that belongs to a server with
        Bedrock disabled is rejected instead of being silently reassigned to another server.
        """
        instances = list(self.m.instances.values())
        if not any(i.cfg.bedrock_enabled for i in instances):
            return {}

        settings = self.m.settings
        domains = _domains(settings)
        base = domains[0] if domains else ""
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
        if domains:
            for inst in instances:
                for host in _domain_hosts(inst, domains):  # the domain the server shows comes first
                    add(host, inst)
        elif public and not _is_ip(public):
            for inst in instances:
                add(f"{inst.cfg.subdomain}.{public}", inst)

        default = self._default_server()
        if default is not None:
            for root in (*domains, public):
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
        # A domain is only usable when it has a DNS record – Geyser does not start signaling otherwise,
        # and a wildcard record (*.example.com) does not cover the bare domain itself.
        if public and valid_host(public) and (_is_ip(public) or _resolves(public)):
            return [f"[{public}]" if ":" in public else public], "public_address"
        # Advertise every Bedrock server's own player address, so each server's card shows the same
        # address for players and for the game connection. All of them point at this machine.
        hosts: list[str] = []
        for inst in self._bedrock_servers():
            host = self.public_host(inst)
            if host and not _is_ip(host) and host not in hosts and _resolves(host):
                hosts.append(host)
        if hosts:
            return hosts, "hostname"
        # e.g. only a bare base domain or an alias resolves
        roots = {*_domains(s), public}
        for host in sorted(self.route_map(), key=lambda h: (h in roots, h)):
            if not _is_ip(host) and _resolves(host):
                return [host], "hostname"
        return [], "stun"

    def _bedrock_servers(self) -> list["ServerInstance"]:
        """Bedrock-enabled servers in a stable order: the default server first, then by id."""
        default = self._default_server()
        enabled = [i for i in self.m.instances.values() if i.cfg.bedrock_enabled]
        return sorted(enabled, key=lambda i: (i is not default, i.cfg.id))

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
        domains, public = _domains(s), normalize_host(s.public_address)
        roots = domains or ([public] if public and not _is_ip(public) else [])
        out: set[str] = set()
        for inst in self.m.instances.values():
            for alias in inst.cfg.aliases:
                h = normalize_host(alias)
                if valid_host(h):
                    out.add(h)
            for root in roots:
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
            + ("" if getattr(self.m.settings, "bedrock_integrated_pack", True) else "  enable-integrated-pack: false\n") +
            "advanced:\n"
            "  java:\n"
            # Geyser prefixes the Java connection with a PROXY v2 header carrying
            # the real Bedrock address. The proxy trusts it only from loopback.
            "    use-haproxy-protocol: true\n"
            "  bedrock:\n"
            "    validate-bedrock-login: false\n"
            "    nethernet-debug: true\n"
            "log-player-ip-addresses: false\n"
            "enable-metrics: false\n"
            "debug-mode: true\n"
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

    def geyser_source(self) -> tuple[str, str]:
        """(download project, version) of the Geyser build the panel should run."""
        st = self.m.settings
        preview = (getattr(st, "bedrock_geyser_preview", "") or "").strip()
        if getattr(st, "bedrock_geyser_channel", "stable") == "preview" and GEYSER_PREVIEW_RE.match(preview):
            return GEYSER_PREVIEW_PROJECT, preview
        return GEYSER_STABLE

    def geyser_channel_info(self) -> dict:
        st = self.m.settings
        return {
            "channel": getattr(st, "bedrock_geyser_channel", "stable"),
            "preview": getattr(st, "bedrock_geyser_preview", "") or "",
            "installed_preview": self.geyser_project == GEYSER_PREVIEW_PROJECT,
            "integrated_pack": getattr(st, "bedrock_integrated_pack", True),
        }

    def recheck_geyser(self) -> None:
        """Look for the configured build on the next sync (e.g. after switching stable/preview)."""
        self._geyser_check_at = 0.0
        self._geyser_source_checked = None
        self.request_apply()

    async def _update_geyser(self) -> None:
        """Install the newest build of the configured channel and atomically replace the old JAR.

        A test build is never compared with a release by version number – the marker file remembers which
        project and build the JAR came from, so switching channels always swaps the JAR.
        """
        now = time.monotonic()
        source = self.geyser_source()
        if (GEYSER_JAR.exists() and source == self._geyser_source_checked
                and now - self._geyser_check_at < GEYSER_CHECK_INTERVAL):
            return
        project, version = source
        if GEYSER_JAR.exists() and _read_marker() is None and project == GEYSER_STABLE[0]:
            # A JAR from before the marker file: learn its version from its metadata. If it is not a valid
            # Geyser archive, leave it untouched and let the process start report the actual problem.
            self._read_geyser_java_version()
            if not self.geyser_version:
                return
        try:
            meta = await net.get_json(f"{DOWNLOAD_API}/{project}/versions/{version}/builds/latest")
            item = (meta.get("downloads") or {}).get("standalone")
            if not item:
                raise RuntimeError(f"Brak pliku Geyser Standalone dla {project} {version}")
        except Exception as exc:  # noqa: BLE001
            if not GEYSER_JAR.exists():
                raise
            # Keep the installed build when the download server is unreachable; try again later.
            self._geyser_check_at = now
            self._geyser_source_checked = source
            log.warning("Geyser update check failed (%s); keeping the installed build", exc)
            return
        self._geyser_check_at = now
        self._geyser_source_checked = source
        wanted = f"{meta['version']}-b{meta['build']}"
        if GEYSER_JAR.exists():
            installed = _read_marker()
            if installed is None and project == GEYSER_STABLE[0]:
                # Installed before the marker existed: a release JAR reports its own version.
                if self.geyser_version == wanted:
                    _write_marker(project, wanted)
                    self.geyser_project = project
                    return
            elif installed == (project, wanted):
                return
        url = f"{DOWNLOAD_API}/{project}/versions/{meta['version']}/builds/{meta['build']}/downloads/standalone"
        tmp = TMP_DIR / "geyser-standalone.jar.download"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        await net.download(url, tmp, sha256=item.get("sha256"))
        GEYSER_JAR.parent.mkdir(parents=True, exist_ok=True)
        os.replace(tmp, GEYSER_JAR)
        _write_marker(project, wanted)
        self.geyser_project, self.geyser_version = project, wanted
        self._geyser_changed = True
        label = f"testowa {wanted}" if project == GEYSER_PREVIEW_PROJECT else wanted
        self._append_log(f"Pobrano Geyser {label}")
        log.info("Installed Geyser %s %s", project, wanted)

    async def send_command(self, command: str) -> None:
        """Type a command into Geyser's console (e.g. ``geyser nethernet stats <player>``)."""
        proc = self.proc
        if not self.running or proc is None or proc.stdin is None:
            raise RuntimeError("Geyser nie działa – włącz Bedrock dla serwera")
        self._append_log(f"> {command}")
        proc.stdin.write((command + "\n").encode("utf-8"))
        await proc.stdin.drain()

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
        if not text:
            return
        self.log_tail.append(text)
        self._log_seq += 1
        self.log_entries.append({"seq": self._log_seq, "t": time.time(), "text": text})

    def logs(self, after: int = 0) -> dict:
        """Structured Geyser output for the panel, with the current gateway state.

        ``after`` returns only entries newer than that sequence number; ``epoch`` changes when the
        buffer is cleared, so clients can replace their copy instead of appending stale lines.
        """
        return {
            "entries": [entry for entry in self.log_entries if entry["seq"] > after],
            "next": self._log_seq,
            "epoch": self._log_epoch,
            "enabled": self.enabled(),
            "running": self.running,
            "listening": self.listening,
            "error": self.error,
            "version": self.geyser_version,
            "host": self.host,
            "port": self.port,
            **self.geyser_channel_info(),
        }

    def clear_logs(self) -> None:
        """Clear the visible output only – the running gateway is not touched."""
        self.log_tail.clear()
        self.log_entries.clear()
        self._log_epoch += 1
