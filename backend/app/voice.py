"""Simple Voice Chat support: one public UDP port shared by every server.

Every SVC packet a client sends starts with a magic byte (0xFF) followed by the player's UUID in
plain text (the rest is encrypted with a per-player secret). That lets the proxy route each player's
datagrams to the right server's internal voice port without touching the payload. Replies from the
server go back to the player through a dedicated upstream socket per player.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid as uuidlib
from pathlib import Path
from typing import TYPE_CHECKING

from .properties import read_properties, write_properties

if TYPE_CHECKING:
    from .manager import Manager, ServerInstance

log = logging.getLogger("minenode.voice")

SVC_PROJECT_ID = "9eGKb6K1"  # Simple Voice Chat on Modrinth
MAGIC = 0xFF
IDLE_TIMEOUT = 90


def config_path(inst: "ServerInstance") -> Path:
    base = "plugins" if inst.cfg.type in ("paper", "purpur") else "config"
    return inst.dir / base / "voicechat" / "voicechat-server.properties"


def is_installed(inst: "ServerInstance") -> bool:
    from .content import content_dir, load_meta, kind_for
    if not kind_for(inst.cfg.type):
        return False
    meta = load_meta(inst)["files"]
    if any(v.get("project_id") == SVC_PROJECT_ID for v in meta.values()):
        return True
    d = content_dir(inst)
    return d.exists() and any(p.name.lower().startswith("voicechat") and p.suffix == ".jar" for p in d.iterdir())


def public_host(inst: "ServerInstance") -> str | None:
    s = inst.m.settings
    if inst.cfg.aliases:
        return inst.cfg.aliases[0]
    if s.base_domain:
        return f"{inst.cfg.subdomain}.{s.base_domain}"
    return s.public_address or None


def write_config(inst: "ServerInstance", public_port: int) -> None:
    """Point SVC at its internal port and tell clients to use the shared public port."""
    if not inst.cfg.voice_enabled or not inst.cfg.voice_port:
        return
    path = config_path(inst)
    path.parent.mkdir(parents=True, exist_ok=True)
    props = read_properties(path)
    host = public_host(inst)
    props.update({
        "port": str(inst.cfg.voice_port),
        "bind_address": "127.0.0.1",
        "voice_host": f"{host}:{public_port}" if host else "",
    })
    write_properties(path, props)


class _Session:
    __slots__ = ("transport", "sid", "last", "packets")

    def __init__(self, transport, sid: str):
        self.transport, self.sid, self.last, self.packets = transport, sid, time.time(), 0


class _Upstream(asyncio.DatagramProtocol):
    def __init__(self, proxy: "VoiceProxy", client: tuple):
        self.proxy, self.client = proxy, client

    def datagram_received(self, data: bytes, addr) -> None:
        sess = self.proxy.sessions.get(self.client)
        if sess:
            sess.last = time.time()
        if self.proxy.transport:
            self.proxy.transport.sendto(data, self.client)
            self.proxy.bytes_down += len(data)


class VoiceProxy(asyncio.DatagramProtocol):
    def __init__(self, manager: "Manager", host: str, port: int):
        self.m = manager
        self.host, self.port = host, port
        self.transport: asyncio.DatagramTransport | None = None
        self.sessions: dict[tuple, _Session] = {}
        self.players: dict[str, str] = {}  # player uuid -> server id
        self._pending: set[tuple] = set()
        self.error: str | None = None
        self.bytes_up = self.bytes_down = self.dropped = 0
        self._janitor: asyncio.Task | None = None

    # ------------------------------------------------------------ routing table
    def bind(self, player_uuid: str | None, sid: str) -> None:
        if player_uuid:
            with contextlib.suppress(ValueError):
                self.players[str(uuidlib.UUID(player_uuid))] = sid

    def route(self, player_uuid: str) -> "ServerInstance | None":
        inst = self.m.instances.get(self.players.get(player_uuid, ""))
        if inst and inst.cfg.voice_enabled and inst.running:
            return inst
        candidates = [i for i in self.m.instances.values() if i.cfg.voice_enabled and i.running and i.cfg.voice_port]
        return candidates[0] if len(candidates) == 1 else None

    def stats(self) -> dict:
        return {"host": self.host, "port": self.port, "listening": self.transport is not None, "error": self.error,
                "sessions": len(self.sessions), "bytes_up": self.bytes_up, "bytes_down": self.bytes_down,
                "per_server": {sid: sum(1 for s in self.sessions.values() if s.sid == sid)
                               for sid in {s.sid for s in self.sessions.values()}}}

    # ------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        try:
            await loop.create_datagram_endpoint(lambda: self, local_addr=(self.host, self.port))
            self._janitor = asyncio.create_task(self._cleanup())
            log.info("Voice chat proxy listening on udp %s:%s", self.host, self.port)
        except OSError as exc:
            self.error = str(exc)
            log.error("Voice proxy could not bind udp %s:%s – %s", self.host, self.port, exc)

    async def stop(self) -> None:
        if self._janitor:
            self._janitor.cancel()
        for s in self.sessions.values():
            s.transport.close()
        self.sessions.clear()
        if self.transport:
            self.transport.close()

    def connection_made(self, transport) -> None:
        self.transport = transport

    async def _cleanup(self) -> None:
        while True:
            await asyncio.sleep(30)
            now = time.time()
            for addr, s in list(self.sessions.items()):
                if now - s.last > IDLE_TIMEOUT or s.sid not in self.m.instances:
                    s.transport.close()
                    self.sessions.pop(addr, None)

    # ------------------------------------------------------------ packets
    def datagram_received(self, data: bytes, addr) -> None:
        self.bytes_up += len(data)
        sess = self.sessions.get(addr)
        if sess:
            sess.last = time.time()
            sess.packets += 1
            sess.transport.sendto(data)
            return
        if len(data) < 17 or data[0] != MAGIC or addr in self._pending:
            self.dropped += 1
            return
        player = str(uuidlib.UUID(bytes=data[1:17]))
        inst = self.route(player)
        if not inst:
            self.dropped += 1
            return
        self._pending.add(addr)
        asyncio.get_running_loop().create_task(self._open(addr, inst, data))

    async def _open(self, addr: tuple, inst: "ServerInstance", first: bytes) -> None:
        try:
            transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(
                lambda: _Upstream(self, addr), remote_addr=("127.0.0.1", inst.cfg.voice_port))
            self.sessions[addr] = _Session(transport, inst.cfg.id)
            transport.sendto(first)
        except OSError as exc:
            log.warning("voice upstream for %s failed: %s", inst.cfg.name, exc)
        finally:
            self._pending.discard(addr)
