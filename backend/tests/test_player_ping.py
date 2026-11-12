import asyncio
import sys
from types import SimpleNamespace

import pytest

from app import proxy
from app.proxy import MinecraftProxy, tcp_rtt_ms


class FakeSock:
    def __init__(self, peer):
        self.peer = peer

    def getpeername(self):
        return self.peer

    def getsockopt(self, *args):
        raise OSError("not a real socket")


def test_player_pings_skip_loopback(monkeypatch):
    p = MinecraftProxy(manager=None, host="127.0.0.1", port=0)
    p.sessions["abc"] = {
        "steve": FakeSock(("203.0.113.7", 50000)),
        "bedrockguy": FakeSock(("127.0.0.1", 50001)),
        "v6local": FakeSock(("::1", 50002, 0, 0)),
    }
    monkeypatch.setattr(proxy, "tcp_rtt_ms", lambda sock: 42)
    assert p.player_pings("abc") == {"steve": 42, "bedrockguy": None, "v6local": None}
    assert p.player_pings("missing") == {}


def test_rtt_of_unreadable_socket_is_none():
    assert tcp_rtt_ms(FakeSock(("203.0.113.7", 1))) is None


@pytest.mark.skipif(not (sys.platform.startswith("linux") or sys.platform == "darwin"),
                    reason="TCP RTT is read only on Linux and macOS")
def test_rtt_of_real_connection_and_session_lifecycle():
    """A piped player is registered while connected, measurable, and removed on disconnect."""

    async def scenario():
        upstream_seen = asyncio.Event()

        async def upstream(reader, writer):  # stands in for the Minecraft server
            upstream_seen.set()
            await reader.read(1)
            writer.close()

        up_server = await asyncio.start_server(upstream, "127.0.0.1", 0)
        up_port = up_server.sockets[0].getsockname()[1]

        p = MinecraftProxy(manager=None, host="127.0.0.1", port=0)
        inst = SimpleNamespace(cfg=SimpleNamespace(id="srv"))
        piped = asyncio.Event()

        async def on_client(reader, writer):
            up_r, up_w = await asyncio.open_connection("127.0.0.1", up_port)
            piped.set()
            await p._pipe(reader, writer, up_r, up_w, inst, "Steve")

        front = await asyncio.start_server(on_client, "127.0.0.1", 0)
        port = front.sockets[0].getsockname()[1]
        c_r, c_w = await asyncio.open_connection("127.0.0.1", port)
        await asyncio.wait_for(piped.wait(), 5)
        await asyncio.wait_for(upstream_seen.wait(), 5)
        for _ in range(50):
            if "srv" in p.sessions:
                break
            await asyncio.sleep(0.01)

        sock = p.sessions["srv"]["steve"]
        rtt = tcp_rtt_ms(sock)  # loopback RTT may round to "no sample" – but must never raise
        assert rtt is None or (isinstance(rtt, int) and rtt >= 1)
        assert p.player_pings("srv") == {"steve": None}  # loopback peers are not reported

        c_w.write(b"x")  # upstream closes after one byte, the player disconnects -> the pipe ends
        await c_w.drain()
        c_w.close()
        for _ in range(200):
            if "srv" not in p.sessions:
                break
            await asyncio.sleep(0.02)
        assert "srv" not in p.sessions
        c_w.close()
        front.close()
        up_server.close()

    asyncio.run(scenario())
