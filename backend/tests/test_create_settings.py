import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app import api, bedrock_compat
from app.models import ServerConfig
from app.properties import read_properties


class FakeManager:
    def __init__(self):
        self.saved = []
        self.bedrock = None
        self.created = []

    def save_server(self, cfg):
        self.saved.append(cfg)


@pytest.fixture
def fake_manager(monkeypatch):
    m = FakeManager()
    monkeypatch.setattr(api, "manager", m, raising=False)
    return m


def test_settings_go_to_server_properties_and_config(tmp_path, fake_manager):
    cfg = ServerConfig(id="s1", name="S", type="paper", mc_version="1.21.1", subdomain="s", internal_port=25610)
    inst = SimpleNamespace(dir=tmp_path, cfg=cfg)
    st = api.CreateSettings(online_mode=False, max_players=42, whitelist=True, auth_enabled=True, gamemode="creative",
                            difficulty="hard", hardcore=True, pvp=False, allow_flight=True, view_distance=6,
                            start_on_join=True, idle_shutdown_minutes=30)
    api._apply_create_settings(inst, st)
    props = read_properties(tmp_path / "server.properties")
    assert props["online-mode"] == "false" and props["max-players"] == "42"
    assert props["white-list"] == "true" and props["enforce-whitelist"] == "true"
    assert (props["gamemode"], props["difficulty"], props["hardcore"], props["pvp"]) == ("creative", "hard", "true", "false")
    assert props["allow-flight"] == "true" and props["view-distance"] == "6"
    assert cfg.start_on_join is True and cfg.idle_shutdown_minutes == 30 and cfg.auth_enabled is True
    assert fake_manager.saved == [cfg]


def test_defaults_match_a_server_made_without_the_wizard():
    st = api.CreateSettings()
    cfg = ServerConfig(id="d", name="D", type="vanilla", mc_version="1.21.1", subdomain="d", internal_port=1)
    assert (st.start_on_join, st.idle_shutdown_minutes, st.auth_enabled) == (cfg.start_on_join, cfg.idle_shutdown_minutes, cfg.auth_enabled)
    assert st.online_mode is True and st.bedrock is False and st.max_players == 20


@pytest.mark.parametrize("bad", [{"max_players": 0}, {"max_players": 5000}, {"view_distance": 64}, {"gamemode": "god"}])
def test_invalid_settings_are_rejected(bad):
    with pytest.raises(Exception):
        api.CreateSettings(**bad)


def test_bedrock_check_uses_the_normal_compatibility_rules(fake_manager):
    mc = bedrock_compat.GEYSER_JAVA_FALLBACK
    assert api._bedrock_check("paper", mc, online_mode=False)["supported"] is True
    online = api._bedrock_check("paper", mc, online_mode=True)
    assert online["supported"] is False and "online-mode" in online["reason"]


def test_create_refuses_incompatible_bedrock_before_creating(fake_manager):
    fake_manager.create = lambda **kw: fake_manager.created.append(kw)
    body = api.CreateServer(name="X", type="paper", mc_version=bedrock_compat.GEYSER_JAVA_FALLBACK, eula=True,
                            settings=api.CreateSettings(online_mode=True, bedrock=True))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(api.create_server(body, "admin"))
    assert exc.value.status_code == 400 and "Bedrock" in exc.value.detail
    assert fake_manager.created == []
