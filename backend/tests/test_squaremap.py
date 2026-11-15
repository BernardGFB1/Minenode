import asyncio
import json
from types import SimpleNamespace

from app import squaremap


def _paper(tmp_path):
    return SimpleNamespace(cfg=SimpleNamespace(type="paper", mc_version="1.21.4", squaremap_enabled=True,
                                               id="srv1", subdomain="survival", name="Survival"),
                           dir=tmp_path)


def test_yaml_set_replaces_only_the_owned_key():
    text = """config-version: 3
settings:
  language-file: lang-en.yml
  internal-webserver:
    enabled: true
    bind: 0.0.0.0
    port: 8080
    flush-json-immediately: false
  web-directory:
    path: custom
    auto-update: true
world-settings:
  default:
    map:
      enabled: true
"""
    out = squaremap._yaml_set(text, ("settings", "internal-webserver", "enabled"), "false")
    assert "    enabled: false" in out
    assert out.count("enabled: false") == 1
    assert out.count("enabled: true") == 1  # only the world map setting remains enabled
    assert "      enabled: true" in out  # the world map setting is untouched
    assert "port: 8080" in out and "path: custom" in out
    assert out.count("internal-webserver:") == 1


def test_yaml_set_creates_missing_blocks():
    out = squaremap._yaml_set("config-version: 3\n", ("settings", "internal-webserver", "enabled"), "false")
    assert "settings:\n  internal-webserver:\n    enabled: false" in out
    again = squaremap._yaml_set(out, ("settings", "internal-webserver", "enabled"), "false")
    assert again == out


def test_yaml_set_adds_missing_child_to_existing_block():
    text = "settings:\n  internal-webserver:\n    bind: 0.0.0.0\n"
    out = squaremap._yaml_set(text, ("settings", "internal-webserver", "enabled"), "false")
    assert "    bind: 0.0.0.0" in out
    assert "    enabled: false" in out
    assert out.index("enabled: false") > out.index("bind:")


def test_write_config_creates_and_repairs_config(tmp_path):
    inst = _paper(tmp_path)
    squaremap.write_config(inst)
    path = tmp_path / "plugins" / "squaremap" / "config.yml"
    assert path.is_file()
    text = path.read_text()
    assert "config-version: 3" in text
    assert "internal-webserver:\n    enabled: false" in text
    assert "flush-json-immediately: true" in text
    assert "web-directory:\n    path: web" in text

    path.write_text(text.replace("enabled: false", "enabled: true"), encoding="utf-8")
    squaremap.write_config(inst)
    assert "enabled: false" in path.read_text()


def test_data_dir_per_platform(tmp_path):
    assert squaremap.data_dir(_paper(tmp_path)) == tmp_path / "plugins" / "squaremap"
    fabric = SimpleNamespace(cfg=SimpleNamespace(type="fabric"), dir=tmp_path)
    assert squaremap.data_dir(fabric) == tmp_path / "squaremap"


def test_installed_info_reads_metadata_and_ignores_addons(tmp_path):
    inst = _paper(tmp_path)
    mods = tmp_path / "plugins"
    mods.mkdir()
    (mods / "squaremap-banner-1.0.jar").write_bytes(b"addon")
    assert squaremap.installed_info(inst) is None

    core = mods / "squaremap-paper-mc1.21.4-1.2.0.jar"
    core.write_bytes(b"core")
    info = squaremap.installed_info(inst)
    assert info["file"] == core.name
    assert info["version_number"] == "1.2.0"
    assert info["managed"] is False

    meta = tmp_path / ".minenode" / "content.json"
    meta.parent.mkdir()
    meta.write_text(json.dumps({"files": {core.name: {
        "project_id": squaremap.PROJECT_ID, "version_id": "abc", "version_number": "1.2.0"}}}))
    info = squaremap.installed_info(inst)
    assert info["managed"] is True and info["version_id"] == "abc"


def test_disable_and_enable_jar_keeps_addons(tmp_path):
    inst = _paper(tmp_path)
    mods = tmp_path / "plugins"
    mods.mkdir()
    core = mods / "squaremap-paper-mc1.21.4-1.2.0.jar"
    addon = mods / "squaremap-banner-1.0.jar"
    core.write_bytes(b"core")
    addon.write_bytes(b"addon")

    squaremap.disable_jar(inst)
    assert (mods / (core.name + ".disabled")).exists()
    assert addon.exists() and not (mods / (addon.name + ".disabled")).exists()

    squaremap.enable_jar(inst)
    assert core.exists()

    old = mods / "squaremap-paper-mc1.21.3-1.1.0.jar"
    old.write_bytes(b"old")
    duplicate = mods / (core.name + ".disabled")
    core.rename(duplicate)
    squaremap._cleanup_old_jars(inst, core.name)
    assert addon.exists() and not old.exists() and not duplicate.exists()


def test_stale_metadata_for_a_deleted_jar_is_ignored(tmp_path):
    inst = _paper(tmp_path)
    meta = tmp_path / ".minenode" / "content.json"
    meta.parent.mkdir()
    meta.write_text(json.dumps({"files": {"squaremap-paper-mc1.21.4-1.2.0.jar": {
        "project_id": squaremap.PROJECT_ID, "version_id": "abc", "version_number": "1.2.0"}}}))
    assert squaremap.installed_info(inst) is None


def test_jar_toggle_survives_duplicate_copies(tmp_path):
    inst = _paper(tmp_path)
    mods = tmp_path / "plugins"
    mods.mkdir()
    active = mods / "squaremap-paper-mc1.21.4-1.2.0.jar"
    active.write_bytes(b"active")

    squaremap.disable_jar(inst)
    disabled = mods / (active.name + ".disabled")
    assert disabled.is_file() and not active.exists()

    active.write_bytes(b"duplicate active")
    squaremap.disable_jar(inst)
    assert not active.exists() and disabled.read_bytes() == b"active"

    disabled.write_bytes(b"duplicate disabled")
    active.write_bytes(b"active again")
    squaremap.enable_jar(inst)
    assert active.read_bytes() == b"active again" and not disabled.exists()


def test_ensure_disables_a_managed_jar_but_keeps_a_manual_one(tmp_path):
    managed = _paper(tmp_path)
    managed.cfg.squaremap_enabled = False
    mods = tmp_path / "plugins"
    mods.mkdir()
    jar = mods / "squaremap-paper-mc1.21.4-1.2.0.jar"
    jar.write_bytes(b"core")
    meta = tmp_path / ".minenode" / "content.json"
    meta.parent.mkdir()
    meta.write_text(json.dumps({"files": {jar.name: {"project_id": squaremap.PROJECT_ID,
                                                     "version_id": "v1", "version_number": "1.2.0"}}}))
    asyncio.run(squaremap.ensure(managed))
    assert not jar.exists() and (mods / (jar.name + ".disabled")).is_file()

    manual = SimpleNamespace(cfg=SimpleNamespace(type="paper", mc_version="1.21.4",
                                                 squaremap_enabled=False, id="m", subdomain="m", name="M"),
                             dir=tmp_path / "manual")
    (manual.dir / "plugins").mkdir(parents=True)
    manual_jar = manual.dir / "plugins" / jar.name
    manual_jar.write_bytes(b"manual")
    asyncio.run(squaremap.ensure(manual))
    assert manual_jar.is_file()  # the panel does not take over manual installs
    assert (manual.dir / "plugins" / "squaremap" / "config.yml").is_file()


def test_ensure_keeps_an_installed_map_when_modrinth_is_down(tmp_path, monkeypatch):
    inst = _paper(tmp_path)
    mods = tmp_path / "plugins"
    mods.mkdir()
    (mods / "squaremap-paper-mc1.21.4-1.2.0.jar.disabled").write_bytes(b"core")

    async def down(_inst, refresh=False):
        raise RuntimeError("network down")

    monkeypatch.setattr(squaremap, "latest_version", down)
    result = asyncio.run(squaremap.ensure(inst))
    assert result["updated"] is False
    assert (mods / "squaremap-paper-mc1.21.4-1.2.0.jar").is_file()


def test_public_lookup_prefers_subdomain_over_name_slug(monkeypatch):
    from app import api

    a = SimpleNamespace(cfg=SimpleNamespace(id="a", subdomain="other", name="Survival"))
    b = SimpleNamespace(cfg=SimpleNamespace(id="b", subdomain="survival", name="Bar"))
    monkeypatch.setattr(api, "manager", SimpleNamespace(instances={"a": a, "b": b}), raising=False)
    assert api._public_inst("survival") is b
    assert api._public_inst("a") is a
    assert api._public_inst("bar") is b


def test_ensure_reenables_an_up_to_date_disabled_jar(tmp_path, monkeypatch):
    inst = _paper(tmp_path)
    mods = tmp_path / "plugins"
    mods.mkdir()
    disabled = mods / "squaremap-paper-mc1.21.4-1.2.0.jar.disabled"
    disabled.write_bytes(b"core")

    async def latest(_inst, refresh=False):
        return {"id": "v1", "version_number": "1.2.0"}, "ok"

    monkeypatch.setattr(squaremap, "latest_version", latest)
    result = asyncio.run(squaremap.ensure(inst))
    assert result["updated"] is False
    assert (mods / "squaremap-paper-mc1.21.4-1.2.0.jar").is_file()
    assert not disabled.exists()
    assert (tmp_path / "plugins" / "squaremap" / "config.yml").is_file()


def test_map_available_requires_enabled_and_index(tmp_path):
    inst = _paper(tmp_path)
    assert squaremap.map_available(inst) is False
    web = tmp_path / "plugins" / "squaremap" / "web"
    web.mkdir(parents=True)
    (web / "index.html").write_text("<!doctype html>")
    assert squaremap.map_available(inst) is True
    inst.cfg.squaremap_enabled = False
    assert squaremap.map_available(inst) is False


def test_state_for_vanilla_is_disabled_with_a_reason(tmp_path):
    inst = SimpleNamespace(cfg=SimpleNamespace(type="vanilla", mc_version="1.21.4", squaremap_enabled=False,
                                               id="v", subdomain="vanilla", name="Vanilla"),
                           dir=tmp_path)
    state = __import__("asyncio").run(squaremap.state(inst))
    assert state["supported"] is False
    assert "Vanilla" in state["reason"]
    assert state["available"] is False and state["error"] is None


def test_state_reports_update_and_public_urls(tmp_path, monkeypatch):
    inst = _paper(tmp_path)
    (tmp_path / "plugins" / "squaremap" / "web").mkdir(parents=True)
    (tmp_path / "plugins" / "squaremap" / "web" / "index.html").write_text("x")
    meta = tmp_path / ".minenode" / "content.json"
    meta.parent.mkdir()
    meta.write_text(json.dumps({"files": {"squaremap-paper-mc1.21.4-1.2.0.jar": {
        "project_id": squaremap.PROJECT_ID, "version_id": "old", "version_number": "1.2.0"}}},
        ))
    (tmp_path / "plugins" / "squaremap-paper-mc1.21.4-1.2.0.jar").write_bytes(b"core")

    async def latest(_inst, refresh=False):
        return {"id": "new", "version_number": "1.3.0"}, "ok"

    monkeypatch.setattr(squaremap, "latest_version", latest)
    state = asyncio.run(squaremap.state(inst))
    assert state["installed"] is True and state["enabled"] is True
    assert state["update_available"] is True
    assert state["map_available"] is True
    assert state["public_url"] == "/survival/stats"
    assert state["map_url"] == "/map/srv1/"
    assert state["data_dir"] == "plugins/squaremap"


def test_public_stats_separates_online_and_offline(tmp_path):
    from app.api import _public_stats

    class Store:
        data = {"known": {
            "steve": {"name": "Steve", "uuid": "u1", "last_seen": 100},
            "alex": {"name": "Alex", "uuid": "u2", "last_seen": 300},
            "old": {"name": "Old", "uuid": "u3", "last_seen": 50},
        }}

    inst = SimpleNamespace(
        cfg=SimpleNamespace(id="srv1", name="Survival", subdomain="survival", type="paper",
                            mc_version="1.21.4", loader_version="1.21.4", squaremap_enabled=True),
        dir=tmp_path, state="online", running=True, started_at=1000.0,
        online={"Steve": 200.0}, players_store=Store(),
        props=lambda: {"max-players": "30", "motd": "Hello"}, address=lambda: "play.example.com",
        online_mode=True, icon_version=lambda: 7,
    )
    stats = _public_stats(inst)
    assert [p["name"] for p in stats["online"]] == ["Steve"]
    assert stats["online"][0]["joined_at"] == 200.0
    assert [p["name"] for p in stats["offline"]] == ["Alex", "Old"]
    assert stats["players"] == {"online": 1, "offline": 2, "max": 30}
    assert stats["server"]["address"] == "play.example.com"
    assert stats["map"]["available"] is False and stats["map"]["url"] is None
