"""Hidden, minimal PicoLimbo authentication server."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

from .auth_sessions import SESSION_FILE
from .config import AUTH_LOBBY_DIR, AUTH_LOBBY_PORT

if TYPE_CHECKING:
    from .manager import Manager

PICO_BINARY = Path(__file__).resolve().parent.parent / "picolimbo-bin" / "pico_limbo"
log = logging.getLogger("minenode.auth_lobby")


class AuthLobby:
    def __init__(self, manager: "Manager"):
        self.m = manager
        self.dir = AUTH_LOBBY_DIR
        self.port = AUTH_LOBBY_PORT
        self.proc: asyncio.subprocess.Process | None = None
        self.error: str | None = None
        self.ready = False
        self._log_task: asyncio.Task | None = None
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
        if not PICO_BINARY.is_file():
            raise RuntimeError("Brak binarki PicoLimbo – zbuduj obraz z backend/picolimbo")
        (self.dir / "server.toml").write_text(
            f'bind = "127.0.0.1:{self.port}"\n'
            'welcome_message = "WYMAGANE LOGOWANIE"\n'
            'action_bar = "WYMAGANE LOGOWANIE"\n'
            'default_game_mode = "spectator"\n'
            'fetch_player_skins = false\n'
            '[title]\n'
            'enabled = true\n'
            'title = "WYMAGANA AUTORYZACJA"\n'
            'subtitle = "Użyj /login lub /register"\n'
            'fade_in = 10\n'
            'stay = 1000000\n'
            'fade_out = 10\n'
            '[forwarding]\nmethod = "NONE"\n', encoding="utf-8")
        SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
        SESSION_FILE.touch(exist_ok=True)

    async def start(self) -> None:
        if self.running:
            return
        try:
            await self._ensure()
            self.proc = await asyncio.create_subprocess_exec(
                str(PICO_BINARY),
                cwd=str(self.dir), stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
            )
            self.error = None
            self.ready = False
            if self._log_task is None or self._log_task.done():
                self._log_task = asyncio.create_task(self._read_output(), name="picolimbo-auth-log")
            if self._watch_task is None or self._watch_task.done():
                self._watch_task = asyncio.create_task(self._watch(), name="auth-lobby-watch")
            for _ in range(360):
                if self.proc.returncode is not None:
                    raise RuntimeError("PicoLimbo lobby zakończyło pracę przed rozpoczęciem nasłuchiwania")
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
                raise RuntimeError("PicoLimbo lobby nie zaczęło nasłuchiwać w ciągu 90 sekund")
        except Exception as exc:  # noqa: BLE001
            self.error = str(exc)
            self.ready = False
            log.exception("Authentication lobby failed to start")
            self.proc = None

    async def stop(self) -> None:
        if self._log_task:
            self._log_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._log_task
            self._log_task = None
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

    async def _read_output(self) -> None:
        proc = self.proc
        if not proc or not proc.stdout:
            return
        try:
            async for raw in proc.stdout:
                text = raw.decode("utf-8", "replace").strip()
                if text:
                    log.info("[PicoLimbo] %s", text)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("Reading PicoLimbo output failed")

    def touch(self, name: str, ip: str) -> None:
        from .auth_sessions import touch
        touch(name, ip)
