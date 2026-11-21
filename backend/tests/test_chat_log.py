import asyncio
import datetime
import gzip
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


def test_chat_history_reads_the_whole_log_not_only_the_console_tail(tmp_path, monkeypatch):
    monkeypatch.setattr(mgr, "SERVERS_DIR", tmp_path)
    inst, _ = _inst()
    inst.cfg = SimpleNamespace(id="srv")
    logs = tmp_path / "srv" / "logs"
    logs.mkdir(parents=True)
    lines = ["[12:00:00] [Server thread/INFO]: <Steve> hello from the beginning"]
    lines += ["[12:00:01] [Server thread/INFO]: Saving the game (this may take a moment!)"] * 400
    (logs / "latest.log").write_text("\n".join(lines) + "\n", encoding="utf-8")
    inst.console = deque(maxlen=100)
    inst._seq = 0
    inst.broadcast = lambda msg: None
    inst._load_log_history()
    assert [(m["name"], m["text"]) for m in inst.chat] == [("Steve", "hello from the beginning")]


def test_chat_history_falls_back_to_rotated_logs(tmp_path, monkeypatch):
    monkeypatch.setattr(mgr, "SERVERS_DIR", tmp_path)
    inst, _ = _inst()
    inst.cfg = SimpleNamespace(id="srv")
    logs = tmp_path / "srv" / "logs"
    logs.mkdir(parents=True)
    (logs / "latest.log").write_text("[12:00:00] [Server thread/INFO]: Done (3.2s)!\n", encoding="utf-8")
    rotated = logs / "2026-11-18-1.log.gz"
    with gzip.open(rotated, "wt", encoding="utf-8") as fh:
        fh.write("[23:59:00] [Server thread/INFO]: <Alex> from the previous session\n")
    t = time.mktime((2026, 11, 19, 0, 5, 0, 0, 0, -1))
    os.utime(rotated, (t, t))
    inst.console = deque(maxlen=100)
    inst._seq = 0
    inst.broadcast = lambda msg: None
    inst._load_log_history()
    assert [(m["name"], m["text"]) for m in inst.chat] == [("Alex", "from the previous session")]


def test_chat_history_keeps_the_latest_messages_across_logs_in_order(tmp_path, monkeypatch):
    monkeypatch.setattr(mgr, "SERVERS_DIR", tmp_path)
    inst, _ = _inst()
    inst.cfg = SimpleNamespace(id="srv")
    logs = tmp_path / "srv" / "logs"
    logs.mkdir(parents=True)
    latest = logs / "latest.log"
    latest.write_text("".join(f"[12:00:00] [Server thread/INFO]: <Steve> new {i}\n" for i in range(1, 31)),
                      encoding="utf-8")
    rotated = logs / "2026-11-18-1.log"
    rotated.write_text("".join(f"[23:59:00] [Server thread/INFO]: <Alex> old {i}\n" for i in range(1, 41)),
                       encoding="utf-8")
    t = time.mktime((2026, 11, 19, 0, 5, 0, 0, 0, -1))
    os.utime(rotated, (t - 100, t - 100))
    os.utime(latest, (t, t))
    inst.console = deque(maxlen=100)
    inst._seq = 0
    inst.broadcast = lambda msg: None
    inst._load_log_history()
    texts = [m["text"] for m in inst.chat]
    assert len(texts) == mgr.CHAT_BUFFER
    assert texts[0] == "old 21"
    assert texts[-1] == "new 30"


def test_chat_history_is_not_duplicated_when_nothing_changed(tmp_path, monkeypatch):
    monkeypatch.setattr(mgr, "SERVERS_DIR", tmp_path)
    inst, _ = _inst()
    inst.cfg = SimpleNamespace(id="srv")
    logs = tmp_path / "srv" / "logs"
    logs.mkdir(parents=True)
    (logs / "latest.log").write_text("[12:00:00] [Server thread/INFO]: <Steve> hello\n", encoding="utf-8")
    inst.console = deque(maxlen=100)
    inst._seq = 0
    inst.broadcast = lambda msg: None
    inst._load_log_history()
    first = [dict(m) for m in inst.chat]
    inst.refresh_chat_history()
    assert [dict(m) for m in inst.chat] == first


def test_empty_chat_cache_is_retried_when_messages_appear(tmp_path, monkeypatch):
    """A first read before anyone spoke must not block later reads of the same log file."""
    monkeypatch.setattr(mgr, "SERVERS_DIR", tmp_path)
    inst, _ = _inst()
    inst.cfg = SimpleNamespace(id="srv")
    logs = tmp_path / "srv" / "logs"
    logs.mkdir(parents=True)
    latest = logs / "latest.log"
    latest.write_text("[12:00:00] [Server thread/INFO]: Done (1.0s)!\n", encoding="utf-8")
    inst.console = deque(maxlen=100)
    inst._seq = 0
    inst.broadcast = lambda msg: None

    inst._load_log_history()
    assert not inst.chat

    # Same file signature, but now it contains chat (and mtime changed).
    latest.write_text("[12:00:00] [Server thread/INFO]: <Steve> finally\n", encoding="utf-8")
    t = time.time() + 1
    os.utime(latest, (t, t))
    inst.refresh_chat_history()
    assert [(m["name"], m["text"]) for m in inst.chat] == [("Steve", "finally")]


def test_public_page_lists_only_offered_lod_mods(tmp_path):
    from app.api import _public_lod
    from app.models import LodSettings
    inst = SimpleNamespace(cfg=SimpleNamespace(type="paper", lod=LodSettings(distant_horizons=True, voxy=True)), dir=tmp_path)
    assert _public_lod(inst) == {"mods": ["distant_horizons", "voxy"]}
    inst.cfg.type = "forge"  # no LOD Server Support for Forge
    assert _public_lod(inst) == {"mods": ["distant_horizons"]}
    inst.cfg.lod = LodSettings()
    assert _public_lod(inst) == {"mods": []}


def test_rotated_log_messages_use_the_date_in_the_file_name(tmp_path):
    path = tmp_path / "2026-11-18-3.log"
    entry = ServerInstance._chat_entry(path, "[23:59:00] [Server thread/INFO]: <Alex> hi", time.time())
    assert entry is not None
    at, name, text = entry
    assert datetime.datetime.fromtimestamp(at).date() == datetime.date(2026, 11, 18)
    assert (name, text) == ("Alex", "hi")
