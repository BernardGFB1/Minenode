"""Server lifecycle, console streaming, monitoring and player tracking."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import shlex
import shutil
import signal
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

import psutil

from . import java as javamod
from . import players as playersmod
from . import providers
from .config import INTERNAL_PORT_START, INTERNAL_VOICE_PORT_START, PANEL_DIR, PROXY_PORT, SERVERS_DIR, VOICE_PORT
from .models import PanelSettings, ServerConfig
from .properties import read_properties, update_properties
from .store import JsonStore

log = logging.getLogger("minenode")

DEFAULT_ICON = Path(__file__).resolve().parent / "assets" / "server-icon.png"
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
CTRL_RE = re.compile(r"\x1b\[[0-9;?]*[ABCDEFGHJKSTfhlnsu]|\x1b\][^\x07]*\x07|\r")
NAME = r"([A-Za-z0-9_]{1,16})"
JOIN_RE = re.compile(rf"\]: {NAME} joined the game")
LEAVE_RE = re.compile(rf"\]: {NAME} left the game")
UUID_RE = re.compile(rf"UUID of player {NAME} is ([0-9a-fA-F-]{{32,36}})")
DONE_RE = re.compile(r"Done \([\d.,]+s\)!")
LIST_RE = re.compile(r"There are (\d+) of a max(?: of)? (\d+) players online:?\s*(.*)$")
WHITELIST_DENY_RES = [
    re.compile(rf"name={NAME}\b.*not white-?listed", re.I),
    re.compile(rf"Disconnecting {NAME} \(.*not white-?listed", re.I),
    re.compile(rf"{NAME} \(/[^)]*\) lost connection: You are not white-?listed", re.I),
    re.compile(rf"{NAME} lost connection: You are not white-?listed", re.I),
]

AIKAR = [
    "-XX:+UseG1GC", "-XX:+ParallelRefProcEnabled", "-XX:MaxGCPauseMillis=200", "-XX:+UnlockExperimentalVMOptions",
    "-XX:+DisableExplicitGC", "-XX:+AlwaysPreTouch", "-XX:G1HeapWastePercent=5", "-XX:G1MixedGCCountTarget=4",
    "-XX:G1MixedGCLiveThresholdPercent=90", "-XX:G1RSetUpdatingPauseTimeTarget=5", "-XX:SurvivorRatio=32",
    "-XX:+PerfDisableSharedMem", "-XX:MaxTenuringThreshold=1",
    "-Dusing.aikars.flags=https://mcflags.emc.gs", "-Daikars.new.flags=true",
]


def jvm_flags(preset: str, max_ram_mb: int, java_feature: int | None) -> list[str]:
    if preset == "none":
        return []
    flags = ["-XX:+IgnoreUnrecognizedVMOptions"]
    if preset == "zgc" and (java_feature is None or java_feature >= 17):
        flags += ["-XX:+UseZGC", "-XX:+AlwaysPreTouch", "-XX:+DisableExplicitGC", "-XX:+PerfDisableSharedMem"]
        if java_feature is None or 21 <= java_feature < 24:
            flags.append("-XX:+ZGenerational")
        return flags
    big = max_ram_mb >= 12 * 1024
    flags += AIKAR + [
        f"-XX:G1NewSizePercent={40 if big else 30}", f"-XX:G1MaxNewSizePercent={50 if big else 40}",
        f"-XX:G1HeapRegionSize={'16M' if big else '8M'}", f"-XX:G1ReservePercent={15 if big else 20}",
        f"-XX:InitiatingHeapOccupancyPercent={20 if big else 15}",
    ]
    return flags


class ServerError(RuntimeError):
    pass


class ServerInstance:
    def __init__(self, manager: "Manager", cfg: ServerConfig):
        self.m = manager
        self.cfg = cfg
        self.state = "offline" if cfg.installed else "not_installed"
        self.proc: asyncio.subprocess.Process | None = None
        self.console: deque[dict] = deque(maxlen=4000)
        self._seq = 0
        self.subs: set[asyncio.Queue] = set()
        self.online: dict[str, float] = {}
        self.stats: deque[dict] = deque(maxlen=450)  # 15 minutes at 2s
        self.job: dict | None = None
        self.started_at: float | None = None
        self.exit_code: int | None = None
        self.java_feature: int | None = None
        self._stop_requested = False
        self._crashes: deque[float] = deque(maxlen=5)
        self._lock = asyncio.Lock()
        self._ps: psutil.Process | None = None
        self._waiters: list[tuple[re.Pattern, asyncio.Future]] = []
        self._tasks: set[asyncio.Task] = set()
        self._props_cache: tuple[float, dict] | None = None
        self._last_daily_restart: str | None = None
        self.empty_since: float | None = None  # for idle auto-shutdown
        self._start_seq = 0
        self._autofix_count = 0
        self.players_store = JsonStore(PANEL_DIR / "players" / f"{cfg.id}.json", lambda: {"attempts": {}, "known": {}})
        self._load_log_history()

    def _load_log_history(self) -> None:
        latest = self.dir / "logs" / "latest.log"
        try:
            if latest.exists() and latest.stat().st_size < 50 * 1024 * 1024:
                with open(latest, "rb") as fh:
                    fh.seek(max(0, latest.stat().st_size - 64 * 1024))
                    lines = fh.read().decode("utf-8", "replace").splitlines()[-200:]
                for line in lines:
                    self._append(line, "out")
                self._append("— powyżej: historia z logs/latest.log —", "panel")
        except OSError:
            pass

    # ------------------------------------------------------------ helpers
    @property
    def dir(self) -> Path:
        return SERVERS_DIR / self.cfg.id

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.returncode is None

    def spawn(self, coro: Awaitable) -> asyncio.Task:
        t = asyncio.create_task(coro)
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)
        return t

    def props(self) -> dict:
        p = self.dir / "server.properties"
        try:
            mtime = p.stat().st_mtime
        except FileNotFoundError:
            return {}
        if self._props_cache and self._props_cache[0] == mtime:
            return self._props_cache[1]
        data = read_properties(p)
        self._props_cache = (mtime, data)
        return data

    @property
    def online_mode(self) -> bool:
        return self.props().get("online-mode", "true").lower() == "true"

    def address(self) -> str:
        s = self.m.settings
        port = "" if PROXY_PORT == 25565 else f":{PROXY_PORT}"
        if self.cfg.aliases:
            return self.cfg.aliases[0] + port
        if s.base_domain:
            return f"{self.cfg.subdomain}.{s.base_domain}{port}"
        host = s.public_address or "twoja-domena"
        return f"{self.cfg.subdomain}.{host}{port}"

    # ------------------------------------------------------------ pub/sub
    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=2000)
        self.subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self.subs.discard(q)

    def broadcast(self, msg: dict) -> None:
        for q in list(self.subs):
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                pass

    def _append(self, text: str, src: str) -> None:
        self._seq += 1
        entry = {"seq": self._seq, "t": time.time(), "text": text, "src": src}
        self.console.append(entry)
        self.broadcast({"type": "log", "entry": entry})

    def panel_log(self, text: str) -> None:
        self._append(text, "panel")

    def set_state(self, state: str) -> None:
        if state != self.state:
            self.state = state
            self.broadcast({"type": "state", "state": state, "summary": self.summary()})

    def set_job(self, title: str | None, stage: str = "", progress: float | None = None) -> None:
        self.job = None if title is None else {"title": title, "stage": stage, "progress": progress}
        self.broadcast({"type": "job", "job": self.job})

    async def wait_for_log(self, pattern: str, timeout: float) -> bool:
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        entry = (re.compile(pattern), fut)
        self._waiters.append(entry)
        try:
            await asyncio.wait_for(fut, timeout)
            return True
        except asyncio.TimeoutError:
            return False
        finally:
            with contextlib.suppress(ValueError):
                self._waiters.remove(entry)

    # ------------------------------------------------------------ summary
    def summary(self) -> dict:
        props = self.props()
        last = self.stats[-1] if self.stats and self.running else None
        return {
            "id": self.cfg.id,
            "name": self.cfg.name,
            "type": self.cfg.type,
            "mc_version": self.cfg.mc_version,
            "loader_version": self.cfg.loader_version,
            "subdomain": self.cfg.subdomain,
            "aliases": self.cfg.aliases,
            "address": self.address(),
            "state": self.state,
            "installed": self.cfg.installed,
            "players_online": len(self.online),
            "players": sorted(self.online.keys(), key=str.lower),
            "max_players": int(mp) if (mp := props.get("max-players", "20")).isdigit() else 20,
            "motd": props.get("motd", "A Minecraft Server"),
            "whitelist": props.get("white-list", "false") == "true",
            "online_mode": props.get("online-mode", "true") == "true",
            "cpu": last["cpu"] if last else 0,
            "mem": last["mem"] if last else 0,
            "max_ram_mb": self.cfg.java.max_ram_mb,
            "started_at": self.started_at if self.running else None,
            "job": self.job,
            "required_java": self.cfg.required_java,
            "java_feature": self.java_feature,
            "internal_port": self.cfg.internal_port,
            "modpack": self.cfg.modpack.model_dump() if self.cfg.modpack else None,
            "has_icon": (self.dir / "server-icon.png").exists(),
            **(self._custom_summary() if self.cfg.type == "custom" else {}),
            "icon_version": self.icon_version(),
            "bedrock_enabled": self.cfg.bedrock_enabled,
            "exit_code": self.exit_code,
            "created_at": self.cfg.created_at,
        }

    # ------------------------------------------------------------ installation
    async def install(self, *, eula: bool, prepare: Callable[[Callable[[str], None]], Awaitable[Any]] | None = None) -> None:
        """Install (or reinstall) the server engine. `prepare` resolves a modpack plan first."""
        if self.running:
            raise ServerError("Zatrzymaj serwer przed instalacją")
        if self.state == "installing":
            raise ServerError("Instalacja już trwa")
        self.set_state("installing")
        self.spawn(self._install(eula, prepare))

    async def _install(self, eula: bool, prepare) -> None:
        log = self.panel_log
        started = time.time()
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            plan = None
            if prepare:
                self.set_job("Instalacja modpacka", "Pobieranie paczki", None)
                plan = await prepare(log)
                self.cfg.type = plan.engine
                self.cfg.mc_version = plan.mc_version
                self.cfg.loader_version = plan.loader_version
                self.m.save_server(self.cfg)
                log(f"Modpack: {plan.name} → {providers.ENGINES[plan.engine]['name']} {plan.mc_version}"
                    + (f" (loader {plan.loader_version})" if plan.loader_version else ""))

            self.set_job("Instalacja", "Sprawdzanie wymagań Javy", 0.1)
            required = await providers.required_java(self.cfg.mc_version)
            self.cfg.required_java = required
            log(f"Minecraft {self.cfg.mc_version} wymaga Java {required}+")
            self.set_job("Instalacja", "Przygotowanie środowiska Java", 0.2)
            feature, java = await javamod.ensure_java(self.cfg.java.version or required, log)
            self.java_feature = feature

            self.set_job("Instalacja", f"Instalacja silnika {providers.ENGINES[self.cfg.type]['name']}", 0.45)
            ctx = providers.InstallContext(self.dir, self.cfg.mc_version, self.cfg.loader_version, java, log)
            launch, loader = await providers.install(self.cfg.type, ctx)
            self.cfg.launch = launch
            self.cfg.loader_version = loader

            if plan:
                self.set_job("Instalacja modpacka", "Pobieranie modów", 0.6)

                def progress(frac: float, stage: str) -> None:
                    self.set_job("Instalacja modpacka", stage, 0.6 + 0.38 * frac)

                await plan.apply(self.dir, log, progress)
                self.set_job("Instalacja modpacka", "Wyłączanie modów tylko dla klienta", 0.98)
                from . import clientmods
                try:
                    if plan.meta.get("server_pack"):
                        log("Oficjalna paczka serwerowa – pomijam automatyczne wyłączanie modów (autor przygotował ją pod serwer).")
                    else:
                        await clientmods.prune(self.dir, log)
                except Exception as exc:  # noqa: BLE001 – a failed check must not fail the install
                    log(f"Nie udało się sprawdzić modów klienckich: {exc}")
                self.set_job("Instalacja modpacka", "Sprawdzanie brakujących zależności", 0.99)
                try:
                    await clientmods.repair_dependencies(self.dir, self.cfg.type, self.cfg.mc_version, log)
                except Exception as exc:  # noqa: BLE001
                    log(f"Nie udało się sprawdzić zależności modów: {exc}")

            if eula:
                (self.dir / "eula.txt").write_text(
                    "# Zaakceptowano przez MineNode (https://aka.ms/MinecraftEULA)\neula=true\n")
            self._enforce_properties(initial=True)
            self.ensure_default_icon()
            self.cfg.installed = True
            self.m.save_server(self.cfg)
            log(f"Instalacja zakończona w {time.time() - started:.0f} s. Serwer jest gotowy do uruchomienia.")
            self.set_job(None)
            self.set_state("offline")
        except Exception as exc:
            self.panel_log(f"BŁĄD instalacji: {exc}")
            logging.getLogger("minenode").exception("install failed")
            self.set_job(None)
            self.set_state("offline" if self.cfg.installed else "install_failed")

    def icon_version(self) -> int:
        """Changes whenever server-icon.png changes – lets the browser drop a cached icon."""
        try:
            return (self.dir / "server-icon.png").stat().st_mtime_ns // 1_000_000
        except OSError:
            return 0

    def set_icon(self, data: bytes) -> None:
        """Replace server-icon.png with an already validated 64×64 PNG (atomic write)."""
        icon = self.dir / "server-icon.png"
        tmp = icon.with_name(".server-icon.png.tmp")
        tmp.write_bytes(data)
        os.replace(tmp, icon)

    def remove_icon(self) -> None:
        (self.dir / "server-icon.png").unlink(missing_ok=True)

    def ensure_default_icon(self) -> bool:
        """Give a server without its own server-icon.png the MineNode icon (64×64 PNG).

        An icon that is already there – uploaded, or shipped by a modpack – is never replaced."""
        icon = self.dir / "server-icon.png"
        if icon.exists() or not DEFAULT_ICON.is_file():
            return False
        try:
            shutil.copyfile(DEFAULT_ICON, icon)
        except OSError as exc:
            logging.getLogger("minenode").warning("Could not write the default server icon: %s", exc)
            return False
        return True

    def _enforce_properties(self, initial: bool = False) -> None:
        path = self.dir / "server.properties"
        changes: dict[str, Any] = {"server-port": self.cfg.internal_port}
        props = read_properties(path)
        if initial:  # a world generator chosen at creation may already have written a few keys
            if "motd" not in props:
                changes["motd"] = f"{self.cfg.name}"
            if "enable-status" not in props:
                changes["enable-status"] = "true"
        if props.get("server-ip"):
            changes["server-ip"] = ""
        if props.get("query.port") == "25565" or (initial and "query.port" not in props):
            changes["query.port"] = self.cfg.internal_port
        update_properties(path, changes)
        if self.cfg.voice_enabled:
            from . import voice
            voice.write_config(self, VOICE_PORT)

    def _custom_summary(self) -> dict:
        try:
            jar = self.custom_jar()
        except ServerError:
            jar = None
        return {"custom_jar": jar, "custom_jars": self.custom_jars()}

    def custom_jars(self) -> list[str]:
        """Server JARs a "Custom JAR" server can start: *.jar files in the server's root folder."""
        try:
            return sorted(p.name for p in self.dir.glob("*.jar") if p.is_file())
        except OSError:
            return []

    def custom_jar(self) -> str:
        """The JAR to start: the one picked in the panel, or the only JAR in the folder."""
        jars = self.custom_jars()
        chosen = self.cfg.launch.target if self.cfg.launch else ""
        if chosen in jars:
            return chosen
        if len(jars) == 1:
            return jars[0]
        if not jars:
            raise ServerError("Wgraj plik .jar silnika do głównego folderu serwera (zakładka Pliki)")
        raise ServerError(f"W folderze serwera jest kilka plików .jar ({', '.join(jars)}) – wybierz silnik w zakładce Java")

    # ------------------------------------------------------------ lifecycle
    def build_command(self, java: Path | str, java_feature: int | None) -> list[str]:
        j = self.cfg.java
        if not self.cfg.launch:
            raise ServerError("Serwer nie jest zainstalowany")
        cmd = [str(java), f"-Xms{min(j.min_ram_mb, j.max_ram_mb)}M", f"-Xmx{j.max_ram_mb}M"]
        cmd += jvm_flags(j.flags_preset, j.max_ram_mb, java_feature)
        cmd += ["-Dterminal.jline=false", "-Dterminal.ansi=true", "-Dlog4j2.formatMsgNoLookups=true",
                "-Dfile.encoding=UTF-8", "-Djava.awt.headless=true"]
        try:
            cmd += shlex.split(j.extra_jvm_args)
            extra_server = shlex.split(j.extra_server_args)
        except ValueError as exc:
            raise ServerError(f"Błędne dodatkowe argumenty: {exc}") from exc
        if self.cfg.type == "custom":
            cmd += ["-jar", self.custom_jar()]
        elif self.cfg.launch.kind == "jar":
            cmd += ["-jar", self.cfg.launch.target]
        else:
            cmd.append(f"@{self.cfg.launch.target}")
        cmd.append("nogui")
        cmd += extra_server
        return cmd

    async def start(self) -> None:
        async with self._lock:
            if self.running:
                return
            if self.state == "installing":
                raise ServerError("Trwa instalacja")
            if not self.cfg.installed:
                raise ServerError("Serwer nie jest zainstalowany")
            eula = self.dir / "eula.txt"
            if not eula.exists() or "eula=true" not in eula.read_text(errors="ignore").replace(" ", "").lower():
                raise ServerError("Nie zaakceptowano EULA Minecrafta (eula.txt)")
            if self.cfg.bedrock_enabled:
                from . import bedrock_compat
                try:
                    compat = await bedrock_compat.ensure(
                        self, getattr(self.m.bedrock, "geyser_java_version", None))
                    if not compat.get("supported"):
                        self.cfg.bedrock_enabled = False
                        self.m.save_server(self.cfg)
                        if self.m.bedrock:
                            self.m.bedrock.request_apply()
                        self.panel_log(f"Bedrock wyłączony: {compat.get('reason', 'brak zgodności')}")
                except Exception as exc:  # Bedrock must not prevent Java startup
                    self.cfg.bedrock_enabled = False
                    self.m.save_server(self.cfg)
                    if self.m.bedrock:
                        self.m.bedrock.request_apply()
                    self.panel_log(f"Bedrock wyłączony – nie udało się przygotować zgodności: {exc}")
            self.set_state("starting")
            try:
                self._enforce_properties()
                required = self.cfg.required_java or await providers.required_java(self.cfg.mc_version)
                feature, java = await javamod.ensure_java(self.cfg.java.version or required, self.panel_log)
                self.java_feature = feature
                cmd = self.build_command(java, feature)
                self.panel_log(f"Uruchamianie serwera (Java {feature}, RAM {self.cfg.java.min_ram_mb}–{self.cfg.java.max_ram_mb} MB)…")
                self.online.clear()
                self._stop_requested = False
                self.exit_code = None
                self.proc = await asyncio.create_subprocess_exec(
                    *cmd, cwd=str(self.dir),
                    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                    limit=1024 * 1024, start_new_session=True,
                    env={**os.environ, "TERM": "xterm-256color"},
                )
            except Exception as exc:
                self.panel_log(f"Nie udało się uruchomić: {exc}")
                self.set_state("crashed")
                raise ServerError(str(exc)) from exc
            self.started_at = time.time()
            self._start_seq = self._seq
            try:
                self._ps = psutil.Process(self.proc.pid)
                self._ps.cpu_percent(None)
            except psutil.Error:
                self._ps = None
            self.stats.clear()
            reader = self.spawn(self._read_output(self.proc))
            self.spawn(self._wait_exit(self.proc, reader))
            from . import content  # local import: content depends on manager types
            self.spawn(content.warn_incompatible(self))

    async def _read_output(self, proc: asyncio.subprocess.Process) -> None:
        assert proc.stdout
        while True:
            try:
                raw = await proc.stdout.readline()
            except ValueError:  # extremely long line – read a chunk instead
                raw = await proc.stdout.read(65536)
            if not raw:
                break
            line = CTRL_RE.sub("", raw.decode("utf-8", "replace").rstrip("\n"))
            while line.startswith("> "):
                line = line[2:]
            if not line.strip() or line.strip() == ">":
                continue
            self._append(line, "out")
            self._parse_line(ANSI_RE.sub("", line))

    def _parse_line(self, plain: str) -> None:
        for pattern, fut in list(self._waiters):
            if not fut.done() and pattern.search(plain):
                fut.set_result(True)
        if self.state == "starting" and DONE_RE.search(plain):
            self.set_state("online")
            self._autofix_count = 0
            self.empty_since = time.time()
            self.panel_log(f"Serwer działa – adres: {self.address()}")
            if self.cfg.idle_shutdown_minutes:
                self.panel_log(f"Auto-wyłączanie: serwer zatrzyma się po {self.cfg.idle_shutdown_minutes} min bez graczy.")
            return
        if m := UUID_RE.search(plain):
            self._remember(m.group(1), m.group(2))
        if m := JOIN_RE.search(plain):
            self.online[m.group(1)] = time.time()
            self._remember(m.group(1), None)
            self._players_changed()
        elif m := LEAVE_RE.search(plain):
            self.online.pop(m.group(1), None)
            self._players_changed()
        elif m := LIST_RE.search(plain):
            names = [n.strip() for n in m.group(3).split(",") if n.strip()]
            self.online = {n: self.online.get(n, time.time()) for n in names if playersmod.NAME_RE.match(n)}
            self._players_changed()
        elif "white" in plain.lower():
            for r in WHITELIST_DENY_RES:
                if m := r.search(plain):
                    self.record_attempt(m.group(1), None, "log")
                    break

    def _players_changed(self) -> None:
        if self.online:
            self.empty_since = None
        elif self.empty_since is None:
            self.empty_since = time.time()
        self.broadcast({"type": "players", "players": sorted(self.online), "count": len(self.online)})

    def _remember(self, name: str, uid: str | None) -> None:
        data = self.players_store.data
        known = data.setdefault("known", {})
        k = name.lower()
        entry = known.get(k, {"name": name})
        entry["name"] = name
        entry["last_seen"] = time.time()
        if uid:
            entry["uuid"] = uid
        known[k] = entry
        self.players_store.save()
        self.bind_voice(name, entry.get("uuid"))

    def bind_voice(self, name: str, uid: str | None) -> None:
        """Tell the voice proxy which server this player is on (UUID is in every SVC packet)."""
        vp = getattr(self.m, "voice", None)
        if not vp or not self.cfg.voice_enabled:
            return
        if not self.online_mode:
            uid = playersmod.offline_uuid(name)
        vp.bind(uid, self.cfg.id)

    def record_attempt(self, name: str, uid: str | None, source: str) -> None:
        if not playersmod.NAME_RE.match(name):
            return
        data = self.players_store.data
        att = data.setdefault("attempts", {})
        e = att.get(name.lower(), {"name": name, "count": 0})
        e.update({"name": name, "ts": time.time(), "source": source, "count": e.get("count", 0) + 1})
        if uid:
            e["uuid"] = uid
        att[name.lower()] = e
        # keep the 200 most recent attempts
        if len(att) > 200:
            for k, _ in sorted(att.items(), key=lambda kv: kv[1].get("ts", 0))[: len(att) - 200]:
                att.pop(k, None)
        self.players_store.save()
        self.broadcast({"type": "attempt", "name": name})

    async def _wait_exit(self, proc: asyncio.subprocess.Process, reader: asyncio.Task) -> None:
        code = await proc.wait()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(reader, 5)
        self.exit_code = code
        self.online.clear()
        self._players_changed()
        self._ps = None
        uptime = time.time() - (self.started_at or time.time())
        if self._stop_requested:
            self.panel_log(f"Serwer zatrzymany (kod {code}).")
            self.set_state("offline")
            return
        self.panel_log(f"Serwer zakończył działanie nieoczekiwanie (kod {code}) po {uptime:.0f} s.")
        self.set_state("crashed")
        if await self._auto_fix_client_mod() or await self._auto_fix_missing_deps():
            return
        now = time.time()
        self._crashes.append(now)
        recent = [t for t in self._crashes if now - t < 600]
        if self.cfg.auto_restart and len(recent) <= 3 and uptime > 15:
            delay = 5 * len(recent)
            self.panel_log(f"Automatyczny restart za {delay} s (próba {len(recent)}/3)…")
            await asyncio.sleep(delay)
            if self.state == "crashed":
                with contextlib.suppress(Exception):
                    await self.start()
        elif self.cfg.auto_restart:
            self.panel_log("Automatyczny restart wstrzymany – zbyt wiele awarii lub awaria przy starcie. Sprawdź konsolę.")

    async def _auto_fix_client_mod(self) -> bool:
        """If a client-only mod crashed the startup, disable it and start again (bounded)."""
        from . import clientmods
        lines = [e["text"] for e in self.console if e["seq"] > self._start_seq and e["src"] == "out"]
        jar = await asyncio.to_thread(clientmods.find_crashing_client_mod, [ANSI_RE.sub("", l) for l in lines], self.dir)
        if not jar or self._autofix_count >= 10:
            return False
        self._autofix_count += 1
        await asyncio.to_thread(clientmods.disable, self.dir, jar, "crash")
        self.panel_log(f"Mod {jar.name} działa tylko po stronie klienta i wysypał serwer – został wyłączony "
                       "(możesz go włączyć z powrotem w zakładce Mody). Ponowne uruchamianie…")
        await asyncio.sleep(2)
        if self.state == "crashed":
            with contextlib.suppress(Exception):
                await self.start()
        return True

    async def _auto_fix_missing_deps(self) -> bool:
        """If startup died on a missing class, install required libraries the modpack forgot (bounded)."""
        from . import clientmods
        if self._autofix_count >= 10 or self.cfg.type not in ("fabric", "quilt", "forge", "neoforge"):
            return False
        lines = [ANSI_RE.sub("", e["text"]) for e in self.console if e["seq"] > self._start_seq and e["src"] == "out"]
        if not clientmods.MISSING_CLASS.search("\n".join(lines[-600:])):
            return False
        self.panel_log("Serwer nie znalazł wymaganej biblioteki – sprawdzam brakujące zależności modów…")
        try:
            added = await clientmods.repair_dependencies(self.dir, self.cfg.type, self.cfg.mc_version, self.panel_log)
        except Exception as exc:  # noqa: BLE001
            self.panel_log(f"Nie udało się sprawdzić zależności: {exc}")
            return False
        if not added:
            self.panel_log("Nie znaleziono brakujących zależności do doinstalowania – sprawdź konsolę.")
            return False
        self._autofix_count += 1
        self.panel_log("Ponowne uruchamianie…")
        await asyncio.sleep(2)
        if self.state == "crashed":
            with contextlib.suppress(Exception):
                await self.start()
        return True

    async def send_command(self, command: str, echo: bool = True) -> None:
        command = command.strip().lstrip("/")
        if not command:
            return
        if not self.running or not self.proc or not self.proc.stdin:
            raise ServerError("Serwer nie działa")
        if echo:
            self._append(command, "cmd")
        self.proc.stdin.write((command + "\n").encode("utf-8"))
        await self.proc.stdin.drain()

    async def stop(self, timeout: float = 90) -> None:
        async with self._lock:
            if not self.running or not self.proc:
                if self.state in ("crashed", "starting"):
                    self.set_state("offline")
                return
            proc = self.proc
            self._stop_requested = True
            self.set_state("stopping")
            self.panel_log("Zatrzymywanie serwera…")
            try:
                await self.send_command("stop", echo=False)
            except Exception:
                pass
            try:
                await asyncio.wait_for(proc.wait(), timeout)
            except asyncio.TimeoutError:
                self.panel_log("Serwer nie odpowiada – wysyłanie SIGTERM…")
                with contextlib.suppress(ProcessLookupError):
                    proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), 15)
                except asyncio.TimeoutError:
                    self.panel_log("Wymuszanie zakończenia (SIGKILL)…")
                    with contextlib.suppress(ProcessLookupError):
                        proc.kill()
                    await proc.wait()
            await asyncio.sleep(0.2)

    async def kill(self) -> None:
        if self.running and self.proc:
            self._stop_requested = True
            self.panel_log("Wymuszone zatrzymanie (kill).")
            with contextlib.suppress(ProcessLookupError):
                os.killpg(self.proc.pid, signal.SIGKILL) if hasattr(os, "killpg") else self.proc.kill()
            await self.proc.wait()

    async def restart(self) -> None:
        await self.stop()
        await self.start()

    def request_start(self) -> bool:
        """Called by the proxy for start-on-join."""
        if self.cfg.start_on_join and self.cfg.installed and self.state in ("offline", "crashed"):
            self.panel_log("Gracz próbuje dołączyć – uruchamiam serwer (start on join).")
            self.spawn(self.start())
            return True
        return False

    # ------------------------------------------------------------ metrics
    def sample(self) -> None:
        if not self.running or not self._ps:
            return
        try:
            with self._ps.oneshot():
                cpu = self._ps.cpu_percent(None)
                mem = self._ps.memory_info().rss
            for child in self._ps.children(recursive=True):
                with contextlib.suppress(psutil.Error):
                    mem += child.memory_info().rss
        except psutil.Error:
            return
        point = {"t": time.time(), "cpu": round(cpu, 1), "mem": mem}
        self.stats.append(point)
        self.broadcast({"type": "stats", "point": point, "players": len(self.online)})


class Manager:
    def __init__(self) -> None:
        self.servers_store = JsonStore(PANEL_DIR / "servers.json", dict)
        self.settings_store = JsonStore(PANEL_DIR / "settings.json", dict)
        self.instances: dict[str, ServerInstance] = {}
        self._tasks: list[asyncio.Task] = []
        self.backups = None  # set in main
        self.proxy = None
        self.voice = None
        self.bedrock = None
        self.auth_lobby = None
        for sid, raw in self.servers_store.data.items():
            try:
                cfg = ServerConfig.model_validate(raw)
                self.instances[sid] = ServerInstance(self, cfg)
            except Exception:
                log.exception("Invalid server config %s – skipped", sid)

    # ------------------------------------------------------------ settings
    @property
    def settings(self) -> PanelSettings:
        return PanelSettings.model_validate(self.settings_store.data or {})

    def save_settings(self, s: PanelSettings) -> None:
        self.settings_store.save(s.model_dump())

    def save_server(self, cfg: ServerConfig) -> None:
        self.servers_store.data[cfg.id] = cfg.model_dump()
        self.servers_store.save()

    # ------------------------------------------------------------ CRUD
    def get(self, sid: str) -> ServerInstance:
        inst = self.instances.get(sid)
        if not inst:
            raise KeyError(sid)
        return inst

    def allocate_voice_port(self) -> int:
        used = {i.cfg.voice_port for i in self.instances.values() if i.cfg.voice_port}
        port = INTERNAL_VOICE_PORT_START
        while port in used or port == VOICE_PORT:
            port += 1
        return port

    def allocate_port(self) -> int:
        used = {i.cfg.internal_port for i in self.instances.values()}
        port = INTERNAL_PORT_START
        while port in used or port == PROXY_PORT:
            port += 1
        return port

    def check_unique(self, subdomain: str, aliases: list[str], exclude: str | None = None) -> None:
        for i in self.instances.values():
            if i.cfg.id == exclude:
                continue
            if i.cfg.subdomain == subdomain:
                raise ServerError(f"Subdomena „{subdomain}” jest już zajęta przez {i.cfg.name}")
            clash = set(aliases) & set(i.cfg.aliases)
            if clash:
                raise ServerError(f"Host {', '.join(clash)} jest już używany przez {i.cfg.name}")

    def create(self, *, name: str, type: str, mc_version: str, loader_version: str | None, subdomain: str,
               java: dict | None = None, modpack: dict | None = None) -> ServerInstance:
        sid = uuid.uuid4().hex[:10]
        cfg = ServerConfig(id=sid, name=name.strip() or "Serwer", type=type, mc_version=mc_version,
                           loader_version=loader_version, subdomain=subdomain,
                           internal_port=self.allocate_port(), java=java or {}, modpack=modpack)
        self.check_unique(cfg.subdomain, cfg.aliases)
        self.save_server(cfg)
        inst = ServerInstance(self, cfg)
        self.instances[sid] = inst
        if not self.settings.default_server_id:
            s = self.settings
            s.default_server_id = sid
            self.save_settings(s)
        return inst

    async def delete(self, sid: str, delete_backups: bool = True) -> None:
        inst = self.get(sid)
        if inst.state == "installing":
            raise ServerError("Poczekaj na zakończenie instalacji")
        if inst.running:
            await inst.stop()
        self.instances.pop(sid, None)
        self.servers_store.data.pop(sid, None)
        self.servers_store.save()
        if self.bedrock:
            self.bedrock.request_apply()
        await asyncio.to_thread(shutil.rmtree, inst.dir, True)
        (PANEL_DIR / "players" / f"{sid}.json").unlink(missing_ok=True)
        if delete_backups and self.backups:
            await asyncio.to_thread(self.backups.delete_all, sid)
        s = self.settings
        if s.default_server_id == sid:
            s.default_server_id = next(iter(self.instances), None)
            self.save_settings(s)

    # ------------------------------------------------------------ routing
    def resolve_host(self, host: str) -> Optional[ServerInstance]:
        host = host.strip().lower().rstrip(".")
        if ":" in host and not host.startswith("["):
            host = host.split(":", 1)[0]
        s = self.settings
        base = s.base_domain.strip().lower().strip(".")
        for inst in self.instances.values():
            if host in inst.cfg.aliases:
                return inst
        sub = None
        if base and host.endswith("." + base):
            sub = host[: -len(base) - 1]
        elif base and host == base:
            sub = None
        elif "." in host and not re.match(r"^[\d.]+$", host):
            sub = host.split(".", 1)[0]
        if sub:
            for inst in self.instances.values():
                if inst.cfg.subdomain == sub:
                    return inst
        if s.default_server_id:
            return self.instances.get(s.default_server_id)
        return None

    # ------------------------------------------------------------ background
    async def startup(self) -> None:
        for inst in self.instances.values():
            if inst.cfg.installed and inst.cfg.auto_start:
                inst.spawn(self._safe_start(inst))
        self._tasks.append(asyncio.create_task(self._sampler()))
        self._tasks.append(asyncio.create_task(self._scheduler()))

    async def _safe_start(self, inst: ServerInstance) -> None:
        try:
            await inst.start()
        except Exception as exc:
            inst.panel_log(f"Autostart nieudany: {exc}")

    async def _sampler(self) -> None:
        while True:
            await asyncio.sleep(2)
            for inst in list(self.instances.values()):
                try:
                    inst.sample()
                except Exception:
                    log.exception("sample failed")

    async def _scheduler(self) -> None:
        while True:
            await asyncio.sleep(30)
            now = time.time()
            hhmm = time.strftime("%H:%M")
            today = time.strftime("%Y-%m-%d")
            for inst in list(self.instances.values()):
                try:
                    b = inst.cfg.backup
                    if (b.enabled and inst.cfg.installed and inst.state != "installing" and self.backups
                            and not self.backups.busy(inst.cfg.id)
                            and now - (inst.cfg.last_backup_at or inst.cfg.created_at) >= b.interval_hours * 3600):
                        inst.spawn(self.backups.create(inst, note="Automatyczna kopia", auto=True))
                    idle = inst.cfg.idle_shutdown_minutes
                    proxy_conns = self.proxy.per_server.get(inst.cfg.id, 0) if self.proxy else 0
                    if (idle and inst.state == "online" and not inst.online and not proxy_conns
                            and inst.empty_since and now - inst.empty_since >= idle * 60
                            and not (self.backups and self.backups.busy(inst.cfg.id))):
                        inst.empty_since = None
                        inst.panel_log(f"Brak graczy od {idle} min – automatyczne wyłączanie serwera."
                                       + (" Uruchomi się sam, gdy ktoś spróbuje dołączyć." if inst.cfg.start_on_join else ""))
                        inst.spawn(inst.stop())
                    if (inst.cfg.restart_daily_at == hhmm and inst.state == "online"
                            and inst._last_daily_restart != today):
                        inst._last_daily_restart = today
                        inst.spawn(self._daily_restart(inst))
                except Exception:
                    log.exception("scheduler failed for %s", inst.cfg.id)

    async def _daily_restart(self, inst: ServerInstance) -> None:
        with contextlib.suppress(Exception):
            await inst.send_command("say Planowany restart serwera za 30 sekund.", echo=False)
            await asyncio.sleep(20)
            await inst.send_command("say Restart za 10 sekund…", echo=False)
            await asyncio.sleep(10)
        inst.panel_log("Planowany codzienny restart.")
        with contextlib.suppress(Exception):
            await inst.restart()

    async def shutdown(self) -> None:
        for t in self._tasks:
            t.cancel()
        running = [i for i in self.instances.values() if i.running]
        if running:
            log.info("Stopping %d server(s)…", len(running))
            await asyncio.gather(*(i.stop(timeout=60) for i in running), return_exceptions=True)
