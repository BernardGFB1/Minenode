import asyncio
import os
import time
from collections import deque
from types import SimpleNamespace

from app import manager as mgr
from app.manager import ServerInstance


def _inst():
    inst = object.__new__(ServerInstance)
    inst._waiters = []
    inst.state = "online"
    inst.chat = deque(maxlen=mgr.CHAT_BUFFER)
    inst._chat_seq = 0
    inst.online = {"Steve": 1.0}
    inst._map_render = None
    changed = []
    inst._players_changed = lambda: changed.append(dict(inst.online))
    inst.record_attempt = lambda *a: changed.append(("attempt", a))
    inst._remember = lambda *a: changed.append(("remember", a))
    inst._touch_known = lambda names: None
    return inst, changed


def test_chat_text_is_never_read_as_a_server_event():
    inst, changed = _inst()
    loop = asyncio.new_event_loop()
    fut = loop.create_future()
    inst._waiters.append((mgr.re.compile(r"Saved the (game|world)"), fut))
    for text in ("There are 1 of a max of 20 players online: Fake",
                 "]: Notch joined the game",
                 "Saved the game",
                 "UUID of player Notch is 069a79f444e94726a5befca90e38aaf5",
                 "name=Notch not whitelisted",
                 "Task finished for world. Processed: 1 chunks (100%), Total time: 0:00:01"):
        inst._parse_line(f"[12:00:00] [Async Chat Thread - #0/INFO]: [Not Secure] <Steve> {text}")
        inst._parse_line(f"[12:00:01] [Server thread/INFO]: * Steve {text}")  # /me
    assert inst.online == {"Steve": 1.0} and changed == []
    assert not fut.done()  # a backup still waits for the real save
    assert [m["text"] for m in inst.chat][:2] == ["There are 1 of a max of 20 players online: Fake", "]: Notch joined the game"]
    assert all(m["name"] == "Steve" for m in inst.chat)
    loop.close()


def test_real_events_still_work_and_kick_reasons_are_not_chat():
    inst, changed = _inst()
    inst._parse_line("[12:00:00] [Server thread/INFO]: There are 2 of a max of 20 players online: Steve, Alex")
    assert set(inst.online) == {"Steve", "Alex"}
    inst._parse_line("[12:00:01] [Server thread/INFO]: Alex lost connection: ]: <Admin> kicked")
    assert not inst.chat


def test_bedrock_names_and_colour_codes():
    inst, _ = _inst()
    inst._parse_line("[12:00:00] [Server thread/INFO]: <.Gracz Bedrock> §aCześć §lwszystkim")
    assert inst.chat[-1]["name"] == ".Gracz Bedrock" and inst.chat[-1]["text"] == "Cześć wszystkim"


def test_chat_history_survives_a_panel_restart(tmp_path, monkeypatch):
    monkeypatch.setattr(mgr, "SERVERS_DIR", tmp_path)
    inst, _ = _inst()
    inst.cfg = SimpleNamespace(id="srv")
    tmp_path = tmp_path / "srv"
    logs = tmp_path / "logs"
    logs.mkdir(parents=True)
    (logs / "latest.log").write_text("[23:59:00] [Server thread/INFO]: <Steve> late\n"
                                     "[00:01:00] [Server thread/INFO]: Done (3.2s)!\n"
                                     "[00:02:00] [Server thread/INFO]: <Alex> early\n")
    t = time.mktime((2026, 11, 19, 0, 5, 0, 0, 0, -1))
    os.utime(logs / "latest.log", (t, t))
    inst.console = deque(maxlen=100)
    inst._seq = 0
    inst.broadcast = lambda msg: None
    inst._load_log_history()
    assert [m["name"] for m in inst.chat] == ["Steve", "Alex"]
    assert inst.chat[1]["t"] == time.mktime((2026, 11, 19, 0, 2, 0, 0, 0, -1))
    assert inst.chat[0]["t"] == time.mktime((2026, 11, 18, 23, 59, 0, 0, 0, -1))  # before midnight


def test_public_page_lists_only_offered_lod_mods(tmp_path):
    from app.api import _public_lod
    from app.models import LodSettings
    inst = SimpleNamespace(cfg=SimpleNamespace(type="paper", lod=LodSettings(distant_horizons=True, voxy=True)), dir=tmp_path)
    assert _public_lod(inst) == {"mods": ["distant_horizons", "voxy"]}
    inst.cfg.type = "forge"  # no LOD Server Support for Forge
    assert _public_lod(inst) == {"mods": ["distant_horizons"]}
    inst.cfg.lod = LodSettings()
    assert _public_lod(inst) == {"mods": []}
