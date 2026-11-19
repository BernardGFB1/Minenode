import asyncio
import json
import zipfile
from types import SimpleNamespace

import pytest

from app import content, lod, managedjar, squaremap, webmap
from app.modpacks import ModpackError
from app.models import LodSettings, ServerConfig, WebMapSettings


def _inst(tmp_path, engine="paper", mc="1.21.11", **webmap_kw):
    sent, logs = [], []

    async def send_command(cmd, echo=True):
        sent.append(cmd)

    inst = SimpleNamespace(
        cfg=SimpleNamespace(type=engine, mc_version=mc, id="srv1", subdomain="survival", name="Survival",
                            webmap=WebMapSettings(enabled=True, **webmap_kw), lod=LodSettings()),
        dir=tmp_path, state="online", running=True, props=lambda: {"level-name": "world"},
        send_command=send_command, panel_log=logs.append, spawn=lambda coro: asyncio.run(coro))
    return inst, sent, logs


# ---------------------------------------------------------------- settings model

def test_old_squaremap_switch_becomes_the_web_map_setting():
    base = {"id": "a", "name": "A", "type": "paper", "mc_version": "1.21.4", "subdomain": "a", "internal_port": 25600}
    cfg = ServerConfig.model_validate({**base, "squaremap_enabled": True, "squaremap_idle_render": True})
    assert cfg.webmap.enabled and cfg.webmap.provider == "squaremap" and cfg.squaremap_enabled
    assert "squaremap_enabled" not in cfg.model_dump()
    cfg.webmap.provider = "bluemap"
    assert not cfg.squaremap_enabled  # the squaremap module only acts when squaremap is the chosen map
    assert not ServerConfig.model_validate(base).webmap.enabled
    assert ServerConfig.model_validate(base).webmap.render_3d is False  # the light 2D view by default


def test_engine_support_matrix(tmp_path):
    def ok(engine, pid, mc="1.21.11"):
        return webmap.engine_supported(_inst(tmp_path, engine, mc)[0], pid)
    assert ok("paper", "dynmap") and ok("fabric", "dynmap") and not ok("neoforge", "dynmap")
    assert ok("neoforge", "bluemap") and ok("forge", "bluemap")
    assert ok("forge", "squaremap", "1.20.1") and not ok("forge", "squaremap", "1.21.4")
    assert not ok("vanilla", "bluemap")
    assert "NeoForge" in webmap.unsupported_reason(_inst(tmp_path, "neoforge")[0], "dynmap")


def test_dynmap_github_assets_match_their_versions():
    fits = webmap._dynmap_asset_fits
    assert fits("Dynmap-CE-3.9.0-fabric-1.21.9-10.jar", "fabric", "1.21.10")
    assert fits("Dynmap-CE-3.9.0-fabric-1.21.9-10.jar", "fabric", "1.21.9")
    assert not fits("Dynmap-CE-3.9.0-fabric-1.21.9-10.jar", "fabric", "1.21.11")
    assert fits("Dynmap-CE-3.9.0-fabric-26.1.jar", "fabric", "26.1.2")
    assert not fits("Dynmap-CE-3.9.0-fabric-26.1.jar", "fabric", "26.2")
    assert fits("Dynmap-CE-3.9.0-forge-1.21.11.jar", "forge", "1.21.11")
    assert not fits("Dynmap-CE-3.9.0-forge-1.21.11.jar", "fabric", "1.21.11")


# ---------------------------------------------------------------- Dynmap

DYNMAP_CFG = """deftemplatesuffix: hires
components:
  - class: org.dynmap.InternalClientUpdateComponent
    sendhealth: true
    sendposition: true
    allowwebchat: true
display-whitelist: false
#webserver-bindaddress: 0.0.0.0
webserver-port: 8123
disable-webserver: false
"""


def test_dynmap_config_is_loopback_without_web_chat_and_follows_options(tmp_path):
    inst, _, _ = _inst(tmp_path, port=28100, show_players=False)
    cfg = tmp_path / "plugins" / "dynmap" / "configuration.txt"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(DYNMAP_CFG)
    webmap.dynmap_write_config(inst)
    text = cfg.read_text()
    assert "webserver-port: 28100" in text and "webserver-bindaddress: 127.0.0.1" in text
    assert "deftemplatesuffix: lowres" in text
    assert "    allowwebchat: false" in text
    assert "    sendposition: false" in text and "display-whitelist: true" in text
    templates = (cfg.parent / "templates" / "custom-minenode.txt").read_text()
    assert "normal-lowres:" in templates and "nether-lowres:" in templates and "the_end-lowres:" in templates
    assert "name: flat" in templates and "name: surface" not in templates and "cave" not in templates

    inst.cfg.webmap.render_3d = True
    inst.cfg.webmap.show_players = True
    webmap.dynmap_write_config(inst)
    assert "name: surface" in (cfg.parent / "templates" / "custom-minenode.txt").read_text()
    text = cfg.read_text()
    assert "    sendposition: true" in text and "display-whitelist: false" in text
    assert "    allowwebchat: false" in text  # web chat stays off


def test_dynmap_config_comes_from_the_jar_before_the_first_start(tmp_path):
    inst, _, _ = _inst(tmp_path, "fabric", port=28101)
    mods = tmp_path / "mods"
    mods.mkdir()
    with zipfile.ZipFile(mods / "Dynmap-3.8-fabric-1.21.11.jar", "w") as zf:
        zf.writestr("configuration.txt", DYNMAP_CFG)
    webmap.dynmap_write_config(inst)
    text = (tmp_path / "dynmap" / "configuration.txt").read_text()
    assert "components:" in text and "webserver-port: 28101" in text


def test_dynmap_world_names_per_platform(tmp_path):
    assert webmap._dynmap_worlds(_inst(tmp_path, "paper")[0]) == ["world", "world_nether", "world_the_end"]
    assert webmap._dynmap_worlds(_inst(tmp_path, "fabric")[0]) == ["world", "DIM-1", "DIM1"]


# ---------------------------------------------------------------- BlueMap

def test_bluemap_first_start_only_generates_configs_then_accepts(tmp_path):
    inst, _, _ = _inst(tmp_path, "paper", port=28102, show_players=False)
    d = tmp_path / "plugins" / "BlueMap"
    assert webmap.bluemap_write_config(inst) is True
    core = (d / "core.conf").read_text()
    assert "accept-download: false" in core and "render-thread-count: 1" in core
    web = (d / "webserver.conf").read_text()
    assert 'ip: "127.0.0.1"' in web and "port: 28102" in web and 'webroot: "bluemap/web"' in web
    assert "default-to-flat-view: true" in (d / "webapp.conf").read_text()
    assert "live-player-markers: false" in (d / "plugin.conf").read_text()

    # BlueMap generated its map files on the first start (3D defaults)
    (d / "maps").mkdir()
    (d / "maps" / "world.conf").write_text('world: "world"\nenable-hires: true\nenable-perspective-view: true\n'
                                           "enable-free-flight-view: true\nenable-flat-view: true\n")
    assert webmap.bluemap_write_config(inst, accept=True) is True
    assert "accept-download: true" in (d / "core.conf").read_text()
    m = (d / "maps" / "world.conf").read_text()
    assert "enable-hires: false" in m and "enable-perspective-view: false" in m and "enable-flat-view: true" in m
    assert webmap.bluemap_write_config(inst) is False  # nothing to change any more, accepted stays

    inst.cfg.webmap.render_3d = True
    assert webmap.bluemap_write_config(inst) is True
    assert "enable-hires: true" in (d / "maps" / "world.conf").read_text()
    assert "default-to-flat-view: false" in (d / "webapp.conf").read_text()


def test_bluemap_keeps_user_settings_in_existing_files(tmp_path):
    inst, _, _ = _inst(tmp_path, "fabric", port=28103)
    d = tmp_path / "config" / "bluemap"
    d.mkdir(parents=True)
    (d / "webserver.conf").write_text('# comment\nenabled: true\nport: 8100\nlog: {\n  file: "x"\n}\n')
    webmap.bluemap_write_config(inst)
    text = (d / "webserver.conf").read_text()
    assert "port: 28103" in text and "port: 8100" not in text and "# comment" in text and 'file: "x"' in text


def test_map_availability_needs_a_running_server_for_proxied_maps(tmp_path):
    inst, _, _ = _inst(tmp_path, "paper", provider="bluemap", port=28104)
    (tmp_path / "bluemap" / "web").mkdir(parents=True)
    (tmp_path / "bluemap" / "web" / "index.html").write_text("<html>")
    assert webmap.map_available(inst) and webmap.proxy_port(inst) == 28104
    inst.running = False
    assert not webmap.map_available(inst)
    inst.cfg.webmap.provider = "squaremap"
    assert webmap.proxy_port(inst) is None


# ---------------------------------------------------------------- manual render

def test_render_runs_world_by_world_only_when_asked(tmp_path):
    inst, sent, _ = _inst(tmp_path, "paper", provider="squaremap")
    r = webmap.MapRender(inst)
    r.tick(10_000)
    assert sent == []  # never on its own
    r.start()
    assert sent == ["squaremap fullrender minecraft:overworld"]
    with pytest.raises(ModpackError):
        r.start()  # already running
    r.on_line("[squaremap] Started full map render for minecraft:overworld")
    assert r.status()["current"] == "minecraft:overworld"
    r.on_line("[squaremap] Finished rendering map for minecraft:overworld")
    assert sent[-1] == "squaremap fullrender minecraft:the_nether"
    r.tick(time_after := 10 ** 12)  # the Nether never confirms: skipped after the timeout
    assert sent[-1] == "squaremap fullrender minecraft:the_end"
    r.on_line("[squaremap] Started full map render for minecraft:the_end")
    r.on_line("[squaremap] Finished rendering map for minecraft:the_end")
    assert not r.running and r.status()["finished_at"]
    assert time_after


def test_dynmap_render_and_cancel(tmp_path):
    inst, sent, _ = _inst(tmp_path, "fabric", provider="dynmap")
    r = webmap.MapRender(inst)
    r.start()
    assert sent == ["dynmap fullrender world"]
    r.on_line("[dynmap] Full render starting on world 'world'...")
    r.on_line("[dynmap] Full render of 'world' finished.")
    assert sent[-1] == "dynmap fullrender DIM-1"
    r.on_line("[dynmap] Full render starting on world 'DIM-1'...")
    r.cancel()
    assert sent[-1] == "dynmap cancelrender DIM-1" and not r.running


def test_bluemap_render_and_offline_server(tmp_path):
    inst, sent, _ = _inst(tmp_path, "paper", provider="bluemap")
    r = webmap.MapRender(inst)
    r.start()
    assert sent == ["bluemap force-update"] and not r.running
    inst.state = "offline"
    with pytest.raises(ModpackError):
        webmap.MapRender(inst).start()


def test_squaremap_player_markers_follow_the_option(tmp_path):
    inst, _, _ = _inst(tmp_path, "paper", show_players=False)
    squaremap.write_config(inst)
    text = (tmp_path / "plugins" / "squaremap" / "config.yml").read_text()
    assert "world-settings:\n  default:\n    player-tracker:\n      enabled: false" in text


# ---------------------------------------------------------------- managed jars

def test_managed_jar_toggles_only_its_own_files(tmp_path):
    inst, _, _ = _inst(tmp_path, "fabric")
    mods = tmp_path / "mods"
    mods.mkdir()
    jar = "lod-server-support-fabric-0.22.0+1.21.11.jar"
    (mods / jar).write_bytes(b"x")
    (mods / "voxy-1.0.jar").write_bytes(b"client mod")
    content.save_meta(inst, {"files": {jar: {
        "project_id": lod.VOXY.project_id, "version_id": "v1", "version_number": "v0.22.0+fabric+mc1.21.11"}}, "hashes": {}})
    info = lod.VOXY.installed_info(inst)
    assert info["managed"] and info["enabled"]
    assert asyncio.run(lod.VOXY.status(SimpleNamespace(cfg=SimpleNamespace(type="vanilla"), dir=tmp_path)))["version"] == "0.22.0"
    assert lod.VOXY.disable(inst)
    assert (mods / (jar + ".disabled")).exists() and (mods / "voxy-1.0.jar").exists()
    lod.VOXY.enable(inst)
    assert (mods / jar).exists()


def test_manual_install_is_not_disabled(tmp_path):
    inst, _, _ = _inst(tmp_path, "paper")
    (tmp_path / "plugins").mkdir()
    (tmp_path / "plugins" / "bluemap-5.16-paper.jar").write_bytes(b"x")
    assert webmap.BLUEMAP.installed_info(inst)["version_number"] == "5.16"
    assert not webmap.BLUEMAP.disable(inst)
    assert (tmp_path / "plugins" / "bluemap-5.16-paper.jar").exists()


def test_version_fallback_rules(tmp_path, monkeypatch):
    calls = []

    async def versions(pid, mc, loaders):
        calls.append(mc)
        if mc:
            return []
        return [{"id": "x", "version_number": "1", "version_type": "release", "game_versions": ["1.21.10", "26.1.2"]}]
    monkeypatch.setattr(content, "modrinth_versions", versions)
    jar = managedjar.ManagedJar("t", "Test", "pid", r"^t\.jar$", {"paper": ["paper"], "fabric": ["fabric"]}, fallback="26")
    with pytest.raises(ModpackError):  # a 1.21.x build runs only on its exact version
        asyncio.run(jar.latest(_inst(tmp_path, "paper", "1.21.11")[0]))
    v, compat = asyncio.run(jar.latest(_inst(tmp_path, "paper", "26.1.1")[0]))
    assert v["id"] == "x" and compat == "warn"
    never = managedjar.ManagedJar("n", "Never", "pid", r"^n\.jar$", {"fabric": ["fabric"]}, fallback="never")
    with pytest.raises(ModpackError):
        asyncio.run(never.latest(_inst(tmp_path, "fabric", "26.1.1")[0]))


# ---------------------------------------------------------------- LOD configs

DH_TOML = """_version = 3
[client]
\tquickEnableRendering = true
\t[client.advanced]
[server]
\tgenerationRequestRateLimit = 20
\tplayerBandwidthLimit = 500

[common]
\t[common.multiThreading]
\t\tnumberOfThreads = 8
"""


def test_toml_set_edits_sections_written_by_distant_horizons():
    out = lod.toml_set(DH_TOML, "server", "generationRequestRateLimit", 4)
    assert "\tgenerationRequestRateLimit = 4" in out and "= 20" not in out
    out = lod.toml_set(out, "server", "globalBandwidthLimit", 1500)
    assert out.index("globalBandwidthLimit = 1500") < out.index("[common]")
    out = lod.toml_set(out, "common.multiThreading", "numberOfThreads", 2)
    assert "\t\tnumberOfThreads = 2" in out and "= 8" not in out
    out = lod.toml_set(out, "common.worldGenerator", "chunkGeneratorMode", "PRE_EXISTING_ONLY")
    assert out.rstrip().endswith('[common.worldGenerator]\n\tchunkGeneratorMode = "PRE_EXISTING_ONLY"')
    assert "_version = 3" in out


def test_dh_mod_config_is_light_and_respects_generation(tmp_path):
    inst, _, _ = _inst(tmp_path, "fabric")
    inst.cfg.lod = LodSettings(distant_horizons=True, distance=128)
    lod.write_dh_mod(inst)
    text = (tmp_path / "config" / "DistantHorizons.toml").read_text()
    assert "maxGenerationRequestDistance = 256" in text  # DH's minimum
    assert "numberOfThreads = 2" in text and 'chunkGeneratorMode = "PRE_EXISTING_ONLY"' in text
    inst.cfg.lod.generate = True
    lod.write_dh_mod(inst)
    assert 'chunkGeneratorMode = "FEATURES"' in (tmp_path / "config" / "DistantHorizons.toml").read_text()


def test_dh_plugin_config_starts_from_the_bundled_default(tmp_path):
    inst, _, _ = _inst(tmp_path, "paper")
    inst.cfg.lod = LodSettings(distant_horizons=True, distance=256)
    (tmp_path / "plugins").mkdir()
    with zipfile.ZipFile(tmp_path / "plugins" / "DistantHorizonsSupport-0.14.0.jar", "w") as zf:
        zf.writestr("config.yml", "database_path: \"{datadir}/data.sqlite\"\nrender_distance: 1024\n"
                                  "#generate_new_chunks: false\nscheduler_threads: 4\nconfig_version: 10\n")
    lod.write_dh_plugin(inst)
    text = (tmp_path / "plugins" / "DHSupport" / "config.yml").read_text()
    assert "render_distance: 256" in text and "scheduler_threads: 2" in text
    assert "\ngenerate_new_chunks: false" in text and "config_version: 10" in text and "database_path" in text


def test_voxy_fresh_and_existing_configs(tmp_path):
    inst, _, _ = _inst(tmp_path, "paper")
    inst.cfg.lod = LodSettings(voxy=True, distance=128)
    lod.write_voxy(inst)
    path = tmp_path / "plugins" / "LodServerSupport" / "lss-server-config.yaml"
    text = path.read_text()
    assert text.startswith("#") and "config_version: 1" in text
    assert '"minecraft:the_nether": 64' in text and '"minecraft:overworld": 128' in text
    assert "  enabled: false" in text and "per_player_mib_per_second: 2" in text

    generated = ("config_version: 1\nlod:\n  distance:\n    default_chunks: 512 # comment\n    by_dimension:\n"
                 '      "minecraft:overworld": 512 # o\n      "minecraft:the_nether": 64\n      "minecraft:the_end": 512\n'
                 "generation:\n  enabled: true\n  concurrency:\n    global: 40\n    per_player: 40\n"
                 "network:\n  bandwidth:\n    per_player_mib_per_second: 25\n    global_mib_per_second: 75\n"
                 "far_players:\n  mode: \"on\"\n")
    path.write_text(generated)
    inst.cfg.lod.distance = 256
    lod.write_voxy(inst)
    text = path.read_text()
    assert "default_chunks: 256" in text and '"minecraft:overworld": 256' in text and '"minecraft:the_end": 256' in text
    assert '"minecraft:the_nether": 64' in text and "global: 4" in text and "per_player: 4" in text
    assert "global_mib_per_second: 8" in text and 'mode: "on"' in text
    assert text.count("generation:") == 1 and text.count("enabled: false") == 1


def test_lod_support_per_engine(tmp_path):
    assert lod.supported(_inst(tmp_path, "paper")[0], "distant_horizons")
    assert lod.dh_jar(_inst(tmp_path, "paper")[0]) is lod.DH_PLUGIN
    assert lod.dh_jar(_inst(tmp_path, "neoforge")[0]) is lod.DH_MOD
    assert not lod.supported(_inst(tmp_path, "forge")[0], "voxy")
    assert "Forge" in lod.unsupported_reason(_inst(tmp_path, "forge")[0], "voxy")
    assert not lod.supported(_inst(tmp_path, "vanilla")[0], "distant_horizons")


def test_lod_prepare_disables_switched_off_addons(tmp_path, monkeypatch):
    inst, _, _ = _inst(tmp_path, "fabric")
    mods = tmp_path / "mods"
    mods.mkdir()
    (mods / "lod-server-support-fabric-0.22.0+1.21.11.jar").write_bytes(b"x")
    (mods / "voxy-server-side-fabric.jar").write_bytes(b"old brand")
    (mods / "voxy-server-side-neoforge.jar").write_bytes(b"installed by hand")
    content.save_meta(inst, {"files": {
        "lod-server-support-fabric-0.22.0+1.21.11.jar": {"project_id": lod.VOXY.project_id},
        "voxy-server-side-fabric.jar": {"project_id": lod.LEGACY_VSS.project_id}}, "hashes": {}})
    asyncio.run(lod.prepare(inst))
    assert (mods / "lod-server-support-fabric-0.22.0+1.21.11.jar.disabled").exists()
    assert not (mods / "voxy-server-side-fabric.jar").exists()  # the panel's old copy of the same mod is gone
    assert (mods / "voxy-server-side-neoforge.jar").exists()  # a manual install stays
    files = json.loads((tmp_path / ".minenode" / "content.json").read_text())["files"]
    assert "voxy-server-side-fabric.jar" not in files and "lod-server-support-fabric-0.22.0+1.21.11.jar" in files


def test_lss_adopts_the_voxy_server_side_settings_file(tmp_path):
    inst, _, _ = _inst(tmp_path, "fabric")
    inst.cfg.lod = LodSettings(voxy=True, distance=64)
    old = tmp_path / "config" / "vss-server-config.yaml"
    old.parent.mkdir(parents=True)
    old.write_text("config_version: 1\nlod:\n  distance:\n    default_chunks: 512\nfar_players:\n  mode: \"opt_in\"\n")
    lod.write_voxy(inst)
    text = (tmp_path / "config" / "lss-server-config.yaml").read_text()
    assert "default_chunks: 64" in text and 'mode: "opt_in"' in text


def test_github_failure_falls_back_to_modrinth_and_reports_clearly(tmp_path, monkeypatch):
    async def github_down(inst):
        raise RuntimeError("403 rate limit exceeded")

    async def versions(pid, mc, loaders):
        if mc == "1.21.4":
            return [{"id": "m1", "version_number": "3.7-beta-8", "version_type": "beta", "game_versions": ["1.21.4"]}]
        return []
    monkeypatch.setattr(content, "modrinth_versions", versions)
    jar = managedjar.ManagedJar("d", "Dynmap", "pid", r"^d\.jar$", {"paper": ["paper"]}, fallback="never",
                                extra=github_down, extra_first=lambda inst: True)
    v, _ = asyncio.run(jar.latest(_inst(tmp_path, "paper", "1.21.4")[0]))
    assert v["id"] == "m1" and v["source"] == "modrinth"
    with pytest.raises(ModpackError):  # a clear 400 for the panel, not a server error
        asyncio.run(jar.latest(_inst(tmp_path, "paper", "1.21.11")[0]))


def test_proxy_passes_precompressed_tiles_through(tmp_path):
    import gzip
    import http.server
    import socket
    import threading

    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient

    from app import api

    body = gzip.compress(b'{"tile": 1}')

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Set-Cookie", "x=1")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = http.server.HTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    inst = SimpleNamespace(running=True)
    app = FastAPI()

    @app.get("/p/{path:path}")
    async def p(path: str, request: Request):
        return await api._proxy_map(inst, port, path, request)

    try:
        r = TestClient(app).get("/p/maps/world/tiles/0/x0/z0.json", headers={"Accept-Encoding": "gzip"})
        assert r.status_code == 200 and r.json() == {"tile": 1}  # decoded by the client: encoding kept
        assert r.headers["content-encoding"] == "gzip" and "set-cookie" not in r.headers
        assert r.headers["x-frame-options"] == "SAMEORIGIN"
        inst.running = False
        assert TestClient(app).get("/p/index.html").status_code == 503
    finally:
        srv.shutdown()
