import asyncio
from types import SimpleNamespace

import pytest

from app import chunky
from app.models import ChunkySettings


def make_inst(tmp_path, engine="paper", **settings):
    sent, logs = [], []

    async def send_command(cmd, echo=True):
        sent.append(cmd)

    inst = SimpleNamespace(
        cfg=SimpleNamespace(type=engine, mc_version="26.3", id="s1", chunky=ChunkySettings(**settings)),
        dir=tmp_path, state="online", online={}, empty_since=None, running=True,
        props=lambda: {"level-name": "world"}, send_command=send_command, panel_log=logs.append,
        spawn=lambda coro: asyncio.run(coro))
    return inst, sent, logs


def test_world_names_follow_the_platform(tmp_path):
    paper, _, _ = make_inst(tmp_path, "purpur")
    fabric, _, _ = make_inst(tmp_path, "fabric")
    assert chunky.world_arg(paper, "the_nether") == "world_nether"
    assert chunky.world_arg(fabric, "the_end") == "minecraft:the_end"


def test_installed_build_is_found_and_chunkyborder_is_not_chunky(tmp_path):
    inst, _, _ = make_inst(tmp_path)
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    (plugins / "ChunkyBorder-Bukkit-1.2.20.jar").write_bytes(b"PK")
    assert chunky.installed_info(inst) is None
    (plugins / "Chunky-Paper-1.5.66.jar").write_bytes(b"PK")
    assert chunky.installed_info(inst)["version_number"] == "1.5.66"
    assert chunky.kind(inst) == "plugin"
    assert chunky.kind(make_inst(tmp_path, "neoforge")[0]) == "mod"


def test_vanilla_and_custom_are_not_supported(tmp_path):
    for engine in ("vanilla", "custom"):
        inst, _, _ = make_inst(tmp_path, engine)
        assert not chunky.engine_supported(inst) and chunky.unsupported_reason(inst)


def test_start_sends_the_selection_and_progress_is_followed(tmp_path):
    inst, sent, logs = make_inst(tmp_path, shape="circle", pattern="spiral", center_x=100, center_z=-50,
                                 radius=3000, only_when_empty=False)
    r = chunky.ChunkyRunner(inst)
    r.start()
    assert sent == ["chunky world world", "chunky shape circle", "chunky pattern spiral", "chunky center 100 -50",
                    "chunky radius 3000", "chunky quiet 10", "chunky start", "chunky confirm"]
    r.on_line("[12:00:00 INFO]: [Chunky] Task started in world for the circle region centered at 100.0, -50.0 with radius 3000.0.")
    r.on_line("[12:00:10 INFO]: [Chunky] Task running for world. Processed: 12,345 chunks (12.34%), "
              "ETA: 0:12:34, Rate: 456.7 cps, Current: 12, -3")
    st = r.status()
    assert st["state"] == "running" and st["percent"] == 12.34 and st["processed"] == 12345 and st["eta"] == "0:12:34"
    r.on_line("[12:30:00 INFO]: [Chunky] Task finished for world. Processed: 100000 chunks (100.00%), Total time: 0:30:00")
    st = r.status()
    assert st["state"] == "finished" and st["percent"] == 100.0 and not r.active
    assert any("zakończone" in line for line in logs)
    # the result survives a panel restart
    assert chunky.ChunkyRunner(inst).status()["state"] == "finished"


def test_only_when_empty_waits_pauses_for_players_and_continues(tmp_path, monkeypatch):
    inst, sent, _ = make_inst(tmp_path, only_when_empty=True)
    now = [10_000.0]
    monkeypatch.setattr(chunky.time, "time", lambda: now[0])
    inst.online, inst.empty_since = {"Steve": 1.0}, None
    r = chunky.ChunkyRunner(inst)
    r.start()
    assert sent == [] and r.status()["state"] == "waiting"  # somebody plays: not started yet
    inst.online, inst.empty_since = {}, now[0]
    r.tick(now[0])
    assert sent == []  # empty, but not for long enough yet
    now[0] += chunky.IDLE_DELAY
    r.tick(now[0])
    assert sent[-2:] == ["chunky start", "chunky confirm"]
    r.on_line("[Chunky] Task started in world for the square region centered at 0.0, 0.0 with radius 2000.0.")
    inst.online = {"Alex": now[0]}
    r.on_players(1)
    assert sent[-1] == "chunky pause" and r.auto_paused
    r.on_line("[Chunky] Task paused for world.")
    inst.online, inst.empty_since = {}, now[0]
    now[0] += chunky.IDLE_DELAY
    r.tick(now[0])
    assert sent[-1] == "chunky continue" and not r.auto_paused


def test_generating_with_players_ignores_joins(tmp_path):
    inst, sent, _ = make_inst(tmp_path, only_when_empty=False)
    inst.online = {"Steve": 1.0}
    r = chunky.ChunkyRunner(inst)
    r.start()
    assert "chunky start" in sent
    r.on_line("[Chunky] Task started in world for the square region centered at 0.0, 0.0 with radius 2000.0.")
    count = len(sent)
    r.on_players(1)
    assert len(sent) == count


def test_manual_pause_is_respected_and_cancel_confirms(tmp_path):
    inst, sent, _ = make_inst(tmp_path, only_when_empty=False)
    r = chunky.ChunkyRunner(inst)
    r.start()
    r.on_line("[Chunky] Task started in world for the square region centered at 0.0, 0.0 with radius 2000.0.")
    r.pause()
    r.on_line("[Chunky] Task paused for world.")
    assert sent[-1] == "chunky pause" and r.status()["state"] == "paused"
    r.tick(99_999)
    assert sent[-1] == "chunky pause"  # the timer never continues a task the user paused
    r.resume()
    assert sent[-1] == "chunky continue"
    r.cancel()
    assert sent[-2:] == ["chunky cancel", "chunky confirm"] and not r.active


def test_restart_continues_the_saved_task(tmp_path):
    inst, sent, _ = make_inst(tmp_path, only_when_empty=False)
    r = chunky.ChunkyRunner(inst)
    r.start()
    r.on_line("[Chunky] Task started in world for the square region centered at 0.0, 0.0 with radius 2000.0.")
    r.on_stop()  # server stopped mid-task
    sent.clear()
    r2 = chunky.ChunkyRunner(inst)  # panel restarted too
    r2.tick(1.0)
    assert sent == ["chunky continue"]


def test_paper_worker_threads_follow_the_multicore_option(tmp_path, monkeypatch):
    inst, _, _ = make_inst(tmp_path, multicore=True)
    monkeypatch.setattr(chunky, "available_cores", lambda: 8)
    assert chunky.apply_paper_threads(inst) is False  # Paper has not created its config yet
    cfg = tmp_path / "config" / "paper-global.yml"
    cfg.parent.mkdir()
    cfg.write_text("_version: 31\nchunk-system:\n  io-threads: -1\n  worker-threads: -1\nmisc:\n  x: 1\n")
    assert chunky.apply_paper_threads(inst)
    text = cfg.read_text()
    assert "  worker-threads: 7" in text and "  io-threads: -1" in text and "_version: 31" in text
    inst.cfg.chunky.multicore = False
    chunky.apply_paper_threads(inst)
    assert "  worker-threads: -1" in cfg.read_text()


def test_prepare_disables_pause_when_empty(tmp_path):
    inst, _, _ = make_inst(tmp_path, "fabric")
    (tmp_path / "server.properties").write_text("pause-when-empty-seconds=60\nmotd=x\n")
    chunky.prepare(inst)
    assert "pause-when-empty-seconds=0" in (tmp_path / "server.properties").read_text()


def test_multicore_is_offered_where_it_works(tmp_path):
    assert chunky.multicore_info(make_inst(tmp_path, "paper")[0])["method"] == "paper"
    assert chunky.multicore_info(make_inst(tmp_path, "quilt")[0])["method"] == "c2me"
    assert chunky.multicore_info(make_inst(tmp_path, "forge")[0])["available"] is False
    with pytest.raises(Exception, match="tylko na Paper"):
        asyncio.run(chunky.set_multicore(make_inst(tmp_path, "forge")[0], True))


def test_state_describes_the_tab(tmp_path, monkeypatch):
    inst, _, _ = make_inst(tmp_path, "fabric")
    inst.chunky_runner = chunky.ChunkyRunner(inst)

    async def latest(*a, **kw):
        return {"id": "v1", "version_number": "1.5.66"}
    monkeypatch.setattr(chunky, "latest_version", latest)
    st = asyncio.run(chunky.state(inst))
    assert st["supported"] and st["kind"] == "mod" and not st["installed"] and st["latest_version"] == "1.5.66"
    assert st["worlds"]["overworld"] == "minecraft:overworld" and st["task"]["state"] == "idle"


def test_an_older_build_is_never_offered_as_an_update():
    assert chunky._newer("1.5.67", "1.5.66")
    assert not chunky._newer("1.4.40", "1.5.66")
    assert not chunky._newer("1.5.66", "1.5.66")
