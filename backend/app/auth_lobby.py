"""Hidden, minimal Paper server used by MineNodeAuth."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shutil
import time
from pathlib import Path
from typing import TYPE_CHECKING

from . import net, providers
from .auth_sessions import SESSION_FILE
from .config import AUTH_LOBBY_DIR, AUTH_LOBBY_PORT, AUTH_LOBBY_RAM_MB, TMP_DIR
from .java import ensure_java

if TYPE_CHECKING:
    from .manager import Manager

PAPER_VERSION = "26.3"
PLUGIN = Path(__file__).resolve().parent.parent / "auth_plugin" / "MineNodeAuth.jar"
log = logging.getLogger("minenode.auth_lobby")
MODRINTH = "https://api.modrinth.com/v2"
# ViaVersion translates newer clients, ViaBackwards translates older clients,
# and ViaRewind adds the legacy 1.7/1.8 protocol range.
VIA_PLUGINS = {
    "viaversion": "P1OZGk5p",
    "viabackwards": "NpvuJQoq",
    "viarewind": "TbHIxhx5",
}


class AuthLobby:
    def __init__(self, manager: "Manager"):
        self.m = manager
        self.dir = AUTH_LOBBY_DIR
        self.port = AUTH_LOBBY_PORT
        self.proc: asyncio.subprocess.Process | None = None
        self.error: str | None = None
        self.ready = False
        self.java: Path | None = None
        self._watch_task: asyncio.Task | None = None

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.returncode is None and self.ready

    def _log(self, text: str) -> None:
        # Keep the lobby intentionally quiet; its console is not exposed as a
        # normal user server, but startup failures remain visible in panel logs.
        if self.error:
            self.error = f"{self.error}\n{text}"[-2000:]

    async def _ensure(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "plugins").mkdir(exist_ok=True)
        # The authentication server is disposable. Never retain a lobby
        # world between starts; Paper will regenerate a tiny void world.
        for world in ("auth", "auth_nether", "auth_the_end"):
            shutil.rmtree(self.dir / world, ignore_errors=True)
        (self.dir / "eula.txt").write_text("eula=true\n", encoding="utf-8")
        props = {
            "server-port": str(self.port), "online-mode": "false", "max-players": "100",
            "view-distance": "2", "simulation-distance": "2", "spawn-protection": "0",
            "allow-flight": "false", "hardcore": "false",
            "level-name": "auth", "motd": "MineNode authentication", "white-list": "false",
            "level-type": "minecraft:the_void", "generate-structures": "false",
            "spawn-monsters": "false", "spawn-animals": "false", "allow-nether": "false",
            "enable-command-block": "false", "pvp": "false", "difficulty": "peaceful",
            "broadcast-console-to-ops": "false", "enable-query": "false", "enable-rcon": "false",
        }
        p = self.dir / "server.properties"
        p.write_text("\n".join(f"{k}={v}" for k, v in props.items()) + "\n", encoding="utf-8")
        if not PLUGIN.is_file():
            raise RuntimeError("Brak MineNodeAuth.jar – zbuduj backend/auth_plugin")
        target = self.dir / "plugins" / "MineNodeAuth.jar"
        if not target.exists() or target.stat().st_size != PLUGIN.stat().st_size:
            target.write_bytes(PLUGIN.read_bytes())
        for name, project in VIA_PLUGINS.items():
            await self._ensure_via_plugin(name, project)
        SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
        SESSION_FILE.touch(exist_ok=True)
        # Download the current Paper build only once.  The hidden lobby is
        # deliberately fixed to a stable version to minimize RAM/startup cost.
        jars = list(self.dir.glob("paper-*.jar"))
        if not jars:
            builds = await providers._paper_builds(PAPER_VERSION)
            if not builds:
                raise RuntimeError(f"Brak buildu Paper {PAPER_VERSION}")
            build = builds[0]
            jar = self.dir / build["name"]
            await net.download(build["url"], jar, sha256=build.get("sha256"))
        _, self.java = await ensure_java(await providers.required_java(PAPER_VERSION), self._log)

    async def _ensure_via_plugin(self, name: str, project: str) -> None:
        """Install the newest Paper-compatible ViaVersion addon once."""
        plugins = self.dir / "plugins"
        if any(p.is_file() and p.name.lower().startswith(name.lower()) and p.name.endswith(".jar")
               for p in plugins.iterdir()):
            return
        data = await net.get_json(f"{MODRINTH}/project/{project}/version",
                                  params={"loaders": '["paper"]', "game_versions": '["26.3"]'})
        if not data:
            data = await net.get_json(f"{MODRINTH}/project/{project}/version",
                                      params={"loaders": '["paper"]'})
        if not data:
            raise RuntimeError(f"Brak wersji {name} zgodnej z Paper {PAPER_VERSION}")
        version = next((v for v in data if v.get("version_type") == "release"), data[0])
        file = next((f for f in version.get("files", []) if f.get("primary")), version["files"][0])
        await net.download(file["url"], plugins / Path(file["filename"]).name,
                           sha1=file.get("hashes", {}).get("sha1"))

    async def start(self) -> None:
        if self.running:
            return
        try:
            await self._ensure()
            jar = next(self.dir.glob("paper-*.jar"))
            self.proc = await asyncio.create_subprocess_exec(
                str(self.java), "-Xms64M", f"-Xmx{AUTH_LOBBY_RAM_MB}M", "-XX:+UseSerialGC",
                "-Dterminal.jline=false", "-Dterminal.ansi=false", "-jar", jar.name, "nogui",
                cwd=str(self.dir), stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
            )
            self.error = None
            self.ready = False
            if self._watch_task is None or self._watch_task.done():
                self._watch_task = asyncio.create_task(self._watch(), name="auth-lobby-watch")
            for _ in range(360):
                if self.proc.returncode is not None:
                    raise RuntimeError("Paper lobby zakończyło pracę przed rozpoczęciem nasłuchiwania")
                try:
                    reader, writer = await asyncio.wait_for(
                        asyncio.open_connection("127.0.0.1", self.port), 0.25)
                    writer.close()
                    await writer.wait_closed()
                    self.ready = True
                    break
                except (OSError, asyncio.TimeoutError):
                    await asyncio.sleep(0.25)
            if not self.ready:
                raise RuntimeError("Paper lobby nie zaczęło nasłuchiwać w ciągu 90 sekund")
        except Exception as exc:  # noqa: BLE001
            self.error = str(exc)
            self.ready = False
            log.exception("Authentication lobby failed to start")
            self.proc = None

    async def stop(self) -> None:
        if self._watch_task:
            self._watch_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._watch_task
            self._watch_task = None
        proc, self.proc = self.proc, None
        self.ready = False
        if proc and proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.terminate()
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(proc.wait(), 10)

    async def _watch(self) -> None:
        while True:
            proc = self.proc
            if proc is None:
                return
            await proc.wait()
            if self.proc is not proc:
                return
            self.proc = None
            self.ready = False
            self.error = "Lobby autoryzacji zakończyło pracę – ponawianie za 5 s"
            await asyncio.sleep(5)
            await self.start()

    def touch(self, name: str, ip: str) -> None:
        from .auth_sessions import touch
        touch(name, ip)
