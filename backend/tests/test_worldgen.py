import json
import zipfile
from types import SimpleNamespace

import pytest

from app import worldgen
from app.manager import ServerInstance
from app.models import ServerConfig
from app.properties import read_properties
from app.worldgen import WorldgenError


@pytest.fixture(autouse=True)
def stage_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(worldgen, "STAGE_DIR", tmp_path / "stage")


def make_zip(path, files):
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return path


MCMETA = json.dumps({"pack": {"pack_format": 48, "description": "Better terrain"}})


def datapack(tmp_path, name="terrain.zip"):
    return make_zip(tmp_path / name, {"pack.mcmeta": MCMETA, "data/terrain/worldgen/noise_settings/x.json": "{}"})


def plugin(tmp_path, name="Terra.jar"):
    return make_zip(tmp_path / name, {"plugin.yml": "name: Terra\nmain: com.dfsek.Terra\nversion: 6\n"})


def fabric_mod(tmp_path, name="tectonic.jar"):
    return make_zip(tmp_path / name, {"fabric.mod.json": json.dumps({"id": "tectonic", "name": "Tectonic"})})


def forge_mod(tmp_path, name="byg.jar"):
    return make_zip(tmp_path / name, {"META-INF/mods.toml": "[[mods]]\nmodId=\"byg\"\n"})


# ---------------------------------------------------------------- recognising files
def test_recognises_datapacks_plugins_and_mods(tmp_path):
    assert worldgen.inspect(datapack(tmp_path))["kind"] == "datapack"
    info = worldgen.inspect(plugin(tmp_path))
    assert info["kind"] == "plugin" and info["name"] == "Terra"
    info = worldgen.inspect(fabric_mod(tmp_path))
    assert info["kind"] == "mod" and info["loaders"] == ["fabric"] and info["name"] == "Tectonic"
    assert worldgen.inspect(forge_mod(tmp_path))["loaders"] == ["forge"]


def test_wrapped_datapack_is_fixed(tmp_path):
    p = make_zip(tmp_path / "w.zip", {"Terralith/pack.mcmeta": MCMETA, "Terralith/data/t/x.json": "{}"})
    info = worldgen.inspect(p)
    assert info["kind"] == "datapack" and info["fixed"] is True
    assert "pack.mcmeta" in zipfile.ZipFile(p).namelist()


@pytest.mark.parametrize("files, message", [
    ({"pack.mcmeta": MCMETA, "assets/minecraft/textures/x.png": "x"}, "paczka zasobów"),
    ({"readme.txt": "hi"}, "Nie rozpoznano"),
])
def test_rejects_resource_packs_and_unknown_archives(tmp_path, files, message):
    with pytest.raises(WorldgenError, match=message):
        worldgen.inspect(make_zip(tmp_path / "x.zip", files))


def test_rejects_non_archives(tmp_path):
    (tmp_path / "x.zip").write_text("not a zip")
    with pytest.raises(WorldgenError, match="nie jest plik"):
        worldgen.inspect(tmp_path / "x.zip")


def test_compatibility_follows_the_engine():
    dp = {"kind": "datapack", "loaders": [], "filename": "a.zip"}
    pl = {"kind": "plugin", "loaders": [], "filename": "Terra.jar"}
    fab = {"kind": "mod", "loaders": ["fabric"], "filename": "t.jar"}
    for engine in worldgen.ENGINE_KINDS:
        assert worldgen.compatibility_error(dp, engine) is None  # datapacks work everywhere
    assert worldgen.compatibility_error(pl, "paper") is None
    assert "plugin" in worldgen.compatibility_error(pl, "fabric")
    assert "plugin" in worldgen.compatibility_error(pl, "vanilla")
    assert worldgen.compatibility_error(fab, "quilt") is None  # Quilt loads Fabric mods
    assert "fabric" in worldgen.compatibility_error(fab, "forge")
    assert worldgen.compatibility_error(fab, "custom") is None


def test_level_type_names_follow_the_minecraft_version():
    assert worldgen.level_type_value("large_biomes", "1.21.1") == "minecraft:large_biomes"
    assert worldgen.level_type_value("large_biomes", "1.16.5") == "largeBiomes"
    assert worldgen.level_type_value("normal", "1.12.2") == "default"
    assert worldgen.level_type_value("terralith:default", "1.21.1") == "terralith:default"
    assert worldgen.level_type_value("", "1.21.1") is None
    with pytest.raises(WorldgenError):
        worldgen.level_type_value("Bad Value!", "1.21.1")


# ---------------------------------------------------------------- staging and applying
def test_stage_validate_and_apply(tmp_path):
    staged = [worldgen.stage(datapack(tmp_path), "Better Terrain v2.zip"),
              worldgen.stage(plugin(tmp_path), "Terra-6.jar")]
    assert staged[0]["filename"] == "Better_Terrain_v2.zip"
    spec = {"level_type": "large_biomes", "seed": "minenode", "generator": "Terra:OVERWORLD",
            "files": [s["token"] for s in staged]}
    files = worldgen.validate(spec, "paper", "1.21.1")

    server = tmp_path / "server"
    cfg = ServerConfig(id="w1", name="World Test", type="paper", mc_version="1.21.1", subdomain="w",
                       internal_port=25601)
    inst = SimpleNamespace(dir=server, cfg=cfg)
    logs = []
    worldgen.apply(inst, spec, files, logs.append)

    assert (server / "world" / "datapacks" / "Better_Terrain_v2.zip").is_file()
    assert (server / "plugins" / "Terra-6.jar").is_file()
    assert "generator: Terra:OVERWORLD" in (server / "bukkit.yml").read_text()
    props = read_properties(server / "server.properties")
    assert props["level-type"] == "minecraft:large_biomes" and props["level-seed"] == "minenode"
    assert not list(worldgen.STAGE_DIR.glob("*.json"))  # staged copies are gone
    assert logs

    # the first install still adds the server name as MOTD and the panel's own keys
    inst.cfg.voice_enabled = False
    ServerInstance._enforce_properties(inst, initial=True)
    props = read_properties(server / "server.properties")
    assert props["motd"] == "World Test" and props["server-port"] == "25601"
    assert props["level-type"] == "minecraft:large_biomes"


def test_validate_refuses_incompatible_files_before_anything_is_created(tmp_path):
    token = worldgen.stage(plugin(tmp_path), "Terra.jar")["token"]
    with pytest.raises(WorldgenError, match="plugin"):
        worldgen.validate({"files": [token]}, "fabric", "1.21.1")
    with pytest.raises(WorldgenError, match="tylko razem z pluginem"):
        worldgen.validate({"generator": "Terra"}, "paper", "1.21.1")
    with pytest.raises(WorldgenError, match="wygasł|Nieprawidłowy"):
        worldgen.validate({"files": ["0" * 24]}, "paper", "1.21.1")
    with pytest.raises(WorldgenError, match="Nieprawidłowy"):
        worldgen.validate({"files": ["../../etc/passwd"]}, "paper", "1.21.1")


# ---------------------------------------------------------------- Modrinth
def version(loaders, url="https://cdn/x.jar", vtype="release", deps=(), filename="x.jar", number="1.0"):
    return {"loaders": loaders, "version_type": vtype, "version_number": number,
            "files": [{"url": url, "filename": filename, "primary": True, "hashes": {}}],
            "dependencies": [{"project_id": d, "dependency_type": "required"} for d in deps]}


def test_pick_version_prefers_the_engines_loader_then_releases():
    loaders = worldgen.MODRINTH_LOADERS["fabric"]  # fabric first, datapack as fallback
    dp, beta, rel = version(["datapack"], number="dp"), version(["fabric"], vtype="beta", number="b"), version(["fabric"], number="r")
    assert worldgen.pick_version([dp, beta, rel], loaders)[0]["version_number"] == "r"
    assert worldgen.pick_version([dp], loaders)[0]["version_number"] == "dp"
    assert worldgen.pick_version([version(["forge"])], loaders) is None


def test_modrinth_pulls_required_dependencies(tmp_path, monkeypatch):
    import asyncio
    from app import net
    projects = {
        "AAAAAAAA": ({"title": "Biomes", "server_side": "required"}, [version(["fabric"], deps=["BBBBBBBB", "CCCCCCCC"], filename="biomes.jar")]),
        "BBBBBBBB": ({"title": "TerraBlender", "server_side": "required"}, [version(["fabric"], filename="tb.jar")]),
        "CCCCCCCC": ({"title": "Client Only", "server_side": "unsupported"}, [version(["fabric"], filename="client.jar")]),
    }

    async def get_json(url, params=None):
        pid = url.split("/project/")[1].split("/")[0]
        meta, versions = projects[pid]
        return versions if url.endswith("/version") else meta

    async def download(url, dest, **kw):
        fabric_mod(tmp_path, "src.jar").replace(dest)
        return dest

    monkeypatch.setattr(net, "get_json", get_json)
    monkeypatch.setattr(net, "download", download)
    res = asyncio.run(worldgen.stage_from_modrinth("AAAAAAAA", "fabric", "1.21.1"))
    assert [i["title"] for i in res["items"]] == ["Biomes", "TerraBlender"]  # client-only dependency skipped
    assert res["items"][1]["required_by"] == "Biomes"
    assert all(i["token"] and i["kind"] == "mod" for i in res["items"])
    with pytest.raises(WorldgenError, match="nie ma wersji"):
        asyncio.run(worldgen.stage_from_modrinth("AAAAAAAA", "forge", "1.21.1"))
    with pytest.raises(WorldgenError, match="Nieprawidłowy"):
        asyncio.run(worldgen.stage_from_modrinth("../etc", "fabric", "1.21.1"))
