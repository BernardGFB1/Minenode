import asyncio
import json
from types import SimpleNamespace

from app import api
from app.api import _public_stats
from app.manager import ServerError
from app.models import PublicPageSettings, ServerConfig, WebMapSettings


def _config(**public_page):
    return ServerConfig(id="srv1", name="Survival", type="paper", mc_version="1.21.4",
                        subdomain="survival", internal_port=25610,
                        public_page=PublicPageSettings(**public_page))


def _instance(tmp_path, **public_page):
    class Store:
        data = {"known": {
            "steve": {"name": "Steve", "uuid": "u1", "last_seen": 100},
            "alex": {"name": "Alex", "uuid": "u2", "last_seen": 300},
        }}

    return SimpleNamespace(
        cfg=SimpleNamespace(id="srv1", name="Survival", subdomain="survival", type="paper",
                            mc_version="1.21.4", loader_version="1.21.4",
                            webmap=WebMapSettings(), public_page=PublicPageSettings(**public_page)),
        dir=tmp_path, state="online", running=True, started_at=1000.0,
        online={"Steve": 200.0}, players_store=Store(),
        props=lambda: {"max-players": "30"}, address=lambda: "play.example.com",
        online_mode=True, icon_version=lambda: 0,
        chat=[{"seq": 1, "t": 123.0, "name": "Steve", "text": "Cześć!"}],
    )


def test_public_page_settings_are_visible_by_default():
    page = PublicPageSettings()
    assert page.enabled and page.online_players and page.join_guide and page.chat
    assert page.recent_players and page.online_count and page.history_players and page.start_button
    assert page.player_stats


def test_public_stats_empties_hidden_lists(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "manager", SimpleNamespace(proxy=None), raising=False)
    stats = _public_stats(_instance(tmp_path, online_players=False, recent_players=False, chat=False))
    assert stats["online"] == []
    assert stats["offline"] == []
    assert stats["chat"] == []
    assert stats["public_page"] == {
        "enabled": True, "online_players": False, "join_guide": True, "chat": False,
        "recent_players": False, "online_count": True, "history_players": True, "start_button": True,
        "player_stats": True,
    }
    assert [p["name"] for p in stats["online"]] == []


def test_public_stats_still_returns_visible_lists(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "manager", SimpleNamespace(proxy=None), raising=False)
    stats = _public_stats(_instance(tmp_path))
    assert [p["name"] for p in stats["online"]] == ["Steve"]
    assert [p["name"] for p in stats["offline"]] == ["Alex"]
    assert stats["chat"]
    assert stats["public_page"]["enabled"] is True


def test_disabled_public_page_refuses_stats(monkeypatch):
    inst = SimpleNamespace(cfg=_config(enabled=False))
    monkeypatch.setattr(api, "_public_inst", lambda key: inst)
    response = asyncio.run(api.public_server_stats("survival"))
    assert response.status_code == 403


def test_public_page_endpoints_read_and_save_settings(monkeypatch):
    cfg = _config()
    inst = SimpleNamespace(cfg=cfg)
    saved = []
    monkeypatch.setattr(api, "manager", SimpleNamespace(save_server=saved.append), raising=False)
    monkeypatch.setattr(api, "inst_or_404", lambda sid: inst)

    state = asyncio.run(api.public_page_get("srv1", ""))
    assert state["enabled"] is True and state["public_url"] == "/survival/stats"

    state = asyncio.run(api.public_page_set(
        "srv1", api.PublicPageBody(enabled=False, chat=False, start_button=False, player_stats=False), ""))
    assert state["enabled"] is False and state["chat"] is False
    assert state["join_guide"] is True and state["start_button"] is False and state["player_stats"] is False
    assert state["public_url"] == "/survival/stats"
    assert cfg.public_page.enabled is False and cfg.public_page.chat is False
    assert cfg.public_page.start_button is False and cfg.public_page.player_stats is False
    assert saved == [cfg]


def _start_instance(**public_page):
    calls = []

    async def start():
        calls.append(True)
        inst.state = "starting"

    inst = SimpleNamespace(cfg=_config(**public_page), running=False, state="offline", start=start)
    return inst, calls


def _start(monkeypatch, inst):
    monkeypatch.setattr(api, "_public_inst", lambda key: inst)
    return asyncio.run(api.public_server_start("survival"))


def test_public_start_refused_without_the_start_button(monkeypatch):
    inst, calls = _start_instance(start_button=False)
    response = _start(monkeypatch, inst)
    assert response.status_code == 403
    assert calls == []


def test_public_start_refused_when_the_page_is_disabled(monkeypatch):
    inst, calls = _start_instance(enabled=False)
    response = _start(monkeypatch, inst)
    assert response.status_code == 403
    assert calls == []


def test_public_start_leaves_a_running_server_alone(monkeypatch):
    inst, calls = _start_instance()
    inst.running, inst.state = True, "online"
    response = _start(monkeypatch, inst)
    assert response.status_code == 200
    assert json.loads(response.body) == {"ok": True, "state": "online", "started": False}
    assert calls == []


def test_public_start_starts_a_stopped_server(monkeypatch):
    inst, calls = _start_instance()
    response = _start(monkeypatch, inst)
    assert response.status_code == 200
    assert json.loads(response.body) == {"ok": True, "state": "starting", "started": True}
    assert calls == [True]


def test_public_start_reports_start_errors(monkeypatch):
    async def start():
        raise ServerError("Nie zaakceptowano EULA Minecrafta (eula.txt)")

    inst = SimpleNamespace(cfg=_config(), running=False, state="offline", start=start)
    response = _start(monkeypatch, inst)
    assert response.status_code == 400
    assert "EULA" in json.loads(response.body)["detail"]
