from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app import bedrock
from app.manager import Manager, ServerInstance
from app.models import PanelSettings, ServerConfig


def _settings(**kw):
    return PanelSettings(base_domain="mc.example.com", extra_domains=["play.drugi.pl", "example.com"], **kw)


def _inst(m, sid, sub, aliases=(), domain=None):
    inst = object.__new__(ServerInstance)
    inst.m = m
    inst.cfg = ServerConfig(id=sid, name=sid, type="paper", mc_version="1.21.4", subdomain=sub,
                            aliases=list(aliases), internal_port=25600, domain=domain)
    return inst


def _manager(settings, specs):
    m = object.__new__(Manager)
    m.settings_store = SimpleNamespace(data=settings.model_dump())  # Manager.settings reads it every time
    m.instances = {}
    for spec in specs:
        inst = _inst(m, *spec)
        m.instances[inst.cfg.id] = inst
    return m


def test_domains_list_is_normalised_and_default_first():
    s = PanelSettings(base_domain="MC.Example.com.", extra_domains=["play.drugi.pl", "mc.example.com", " ", "Inna.PL"])
    assert s.domains() == ["mc.example.com", "play.drugi.pl", "inna.pl"]
    assert PanelSettings().domains() == []


def test_every_main_domain_routes_to_the_server():
    m = _manager(_settings(default_server_id="a"), [("a", "survival"), ("b", "creative", ["play.mojserwer.pl"])])
    a, b = m.instances["a"], m.instances["b"]
    assert m.resolve_host("survival.mc.example.com") is a
    assert m.resolve_host("survival.play.drugi.pl") is a
    assert m.resolve_host("creative.play.drugi.pl:25565") is b
    assert m.resolve_host("creative.example.com") is b
    assert m.resolve_host("play.mojserwer.pl") is b  # aliases still win
    assert m.resolve_host("play.drugi.pl") is a  # a bare main domain -> default server
    # "example.com" is also a main domain, but the longer "mc.example.com" decides for its subdomains
    assert m.resolve_host("creative.mc.example.com") is b


def test_shown_address_follows_the_servers_domain_and_falls_back():
    m = _manager(_settings(), [("a", "survival", (), "play.drugi.pl"), ("b", "creative")])
    a, b = m.instances["a"], m.instances["b"]
    assert a.main_domain() == "play.drugi.pl" and a.address().startswith("survival.play.drugi.pl")
    assert b.address().startswith("creative.mc.example.com")
    assert [x.split(":")[0] for x in a.addresses()] == ["survival.play.drugi.pl", "survival.mc.example.com",
                                                        "survival.example.com"]
    m.settings_store.data["extra_domains"] = ["example.com"]  # the chosen domain was removed from the panel
    assert a.main_domain() == "mc.example.com"


def test_bedrock_routes_every_domain_with_the_shown_one_first():
    m = _manager(_settings(default_server_id="a"), [("a", "survival", (), "play.drugi.pl")])
    for inst in m.instances.values():
        inst.cfg.bedrock_enabled = True
    proxy = bedrock.BedrockProxy(m, "0.0.0.0", 19132)
    proxy._bedrock_ready = lambda inst: True
    a = m.instances["a"]
    hosts = proxy.hosts_for(a)
    assert hosts[0] == "survival.play.drugi.pl"
    assert {"survival.mc.example.com", "survival.example.com", "mc.example.com", "play.drugi.pl"} <= set(hosts)
    assert "survival.example.com" in proxy.reserved_hosts()


def test_settings_domains_are_validated():
    from app.api import _clean_domain
    assert _clean_domain(" *.Play.Drugi.PL. ") == "play.drugi.pl"
    assert _clean_domain("") == ""
    with pytest.raises(HTTPException):
        _clean_domain("zła domena!")


def test_creating_or_deleting_servers_never_picks_a_default_server(tmp_path, monkeypatch):
    import asyncio

    from app import manager as mgr

    monkeypatch.setattr(mgr, "SERVERS_DIR", tmp_path / "servers")
    monkeypatch.setattr(mgr, "PANEL_DIR", tmp_path / "panel")
    m = object.__new__(Manager)
    m.settings_store = SimpleNamespace(data={}, save=lambda data=None: None)
    m.servers_store = SimpleNamespace(data={}, save=lambda: None)
    m.instances = {}
    m.bedrock = None
    m.backups = None
    saved = []
    m.save_settings = lambda s: (saved.append(s.default_server_id), m.settings_store.data.update(s.model_dump()))
    m.save_server = lambda cfg: m.servers_store.data.__setitem__(cfg.id, cfg.model_dump())
    monkeypatch.setattr(mgr, "ServerInstance", lambda manager, cfg: SimpleNamespace(
        cfg=cfg, state="offline", running=False, dir=tmp_path / "servers" / cfg.id))
    first = m.create(name="Pierwszy", type="paper", mc_version="1.21.4", loader_version=None, subdomain="pierwszy")
    assert m.settings.default_server_id is None and saved == []
    # a default chosen by the owner is cleared (not moved to another server) when that server is deleted
    m.settings_store.data["default_server_id"] = first.cfg.id
    m.create(name="Drugi", type="paper", mc_version="1.21.4", loader_version=None, subdomain="drugi")
    m.get = lambda sid: m.instances[sid]
    asyncio.run(m.delete(first.cfg.id))
    assert m.settings.default_server_id is None
