"""Login lobby gate: one owner per name, limited failed visits, fail-closed name parsing."""
import time

import pytest

from app import auth_sessions, proxy
from app.proxy import LobbyGate, safe_player_name


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(auth_sessions, "SESSION_FILE", tmp_path / "auth" / "sessions.db")
    monkeypatch.setattr(auth_sessions, "PENDING_FILE", tmp_path / "auth" / "pending.db")


def test_names_that_could_forge_auth_rows_are_refused():
    assert safe_player_name("Steve") and safe_player_name("._Bedrock_Gamer")
    for bad in (None, "", "a\nadmin", "a\tb", "x\r", "a" * 17, "nul\x00"):
        assert not safe_player_name(bad)
    with pytest.raises(ValueError):
        auth_sessions.set_pending("a\nadmin", "203.0.113.9")


def test_second_address_cannot_take_over_a_name_that_is_logging_in():
    gate = LobbyGate()
    assert gate.admit("Steve", "203.0.113.7") is None          # the real Steve is in the lobby
    assert "Ktoś inny" in gate.admit("steve", "198.51.100.66")  # attacker would overwrite pending.db
    assert gate.admit("Steve", "::ffff:203.0.113.7") is None    # Steve reconnecting is fine
    gate.release("Steve", "203.0.113.7")
    gate.release("Steve", "203.0.113.7")
    assert gate.admit("steve", "198.51.100.66") is None         # free again once Steve has left


def test_failed_visits_are_limited_per_address():
    gate = LobbyGate()
    for _ in range(gate.IP_FAILS):
        assert gate.admit("Alex", "198.51.100.66") is None
        gate.release("Alex", "198.51.100.66", failed=True)
    assert "Zbyt wiele" in gate.admit("Notch", "198.51.100.66")
    assert gate.admit("Alex", "203.0.113.1") is None  # other players are not affected


def test_name_limit_never_locks_out_the_accounts_own_address():
    gate = LobbyGate()
    for n in range(gate.NAME_FAILS):
        ip = f"198.51.100.{n}"
        assert gate.admit("Admin", ip) is None
        gate.release("Admin", ip, failed=True)
    assert "Zbyt wiele" in gate.admit("Admin", "198.51.100.200")
    assert gate.admit("Admin", "203.0.113.7", account_ip="203.0.113.7") is None


def test_old_failures_expire(monkeypatch):
    gate = LobbyGate()
    now = time.time()
    for _ in range(gate.IP_FAILS):
        gate.release("Alex", "198.51.100.66", failed=True)
    monkeypatch.setattr(proxy.time, "time", lambda: now + gate.WINDOW + 1)
    assert gate.admit("Alex", "198.51.100.66") is None


def test_backend_listens_on_loopback_only(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from app import manager
    from app.properties import read_properties
    (tmp_path / "server.properties").write_text("server-ip=0.0.0.0\nserver-port=25565\n")
    inst = SimpleNamespace(dir=tmp_path, cfg=SimpleNamespace(internal_port=25601, name="x", voice_enabled=False))
    manager.ServerInstance._enforce_properties(inst)
    props = read_properties(tmp_path / "server.properties")
    assert props["server-ip"] == "127.0.0.1" and props["server-port"] == "25601"


# ---------------------------------------------------------------- routing (fail closed)

class _Writer:
    def __init__(self):
        self.data = b""
        self.closed = False

    def get_extra_info(self, key, default=None):
        return {"peername": ("198.51.100.66", 50000), "sockname": ("0.0.0.0", 25565)}.get(key, default)

    def write(self, data):
        self.data += data

    async def drain(self):
        pass

    def close(self):
        self.closed = True

    async def wait_closed(self):
        pass

    def can_write_eof(self):
        return False


def _login(name_bytes: bytes, protocol=767) -> bytes:
    from app.proxy import mc_string, packet, varint
    hs = packet(0, varint(protocol) + mc_string("survival.example.com") + b"\x63\xdd" + varint(2))
    login = packet(0, varint(len(name_bytes)) + name_bytes + b"\x00" * 16)
    return hs + login


def _route(data: bytes, monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from app.proxy import MinecraftProxy
    started = []
    inst = SimpleNamespace(online_mode=False, cfg=SimpleNamespace(auth_enabled=True, id="s1"),
                           state="online", request_start=lambda: started.append(1) or False)
    lobby = SimpleNamespace(running=True, port=1)
    m = SimpleNamespace(resolve_host=lambda h: inst, auth_lobby=lobby, settings=SimpleNamespace())
    p = MinecraftProxy(m, "127.0.0.1", 0)

    async def go():
        reader = asyncio.StreamReader()
        reader.feed_data(data)
        reader.feed_eof()
        await p._route(reader, _Writer(), ("198.51.100.66", 50000))
    return asyncio.run(go())


def test_login_with_an_unreadable_name_is_refused_not_passed_through(monkeypatch):
    from app.proxy import ProtocolError
    for name in (b"a" * 40, b"evil\nadmin", b""):
        with pytest.raises(ProtocolError):
            _route(_login(name), monkeypatch)
