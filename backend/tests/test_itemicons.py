import asyncio
import io
import json
import shutil
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from app import itemicons
from app.models import PublicPageSettings

REAL_SCHEDULE_BATCH = itemicons.schedule_batch

PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _inst(tmp_path):
    mods = tmp_path / "mods"
    mods.mkdir(parents=True, exist_ok=True)
    rp = tmp_path / ".minenode" / "resourcepacks"
    rp.mkdir(parents=True, exist_ok=True)
    return SimpleNamespace(
        dir=tmp_path,
        cfg=SimpleNamespace(id="srv", type="fabric", mc_version="1.21.4", public_page=PublicPageSettings()),
    )


BLOCK_MODEL = json.dumps({
    "textures": {"all": "minecraft:block/stone"},
    "elements": [{
        "from": [0, 0, 0], "to": [16, 16, 16],
        "faces": {
            face: {"texture": "#all", "uv": [0, 0, 16, 16]}
            for face in ("up", "down", "north", "south", "east", "west")
        },
    }],
})


def _png_bytes(color=(180, 150, 120, 255)):
    buf = io.BytesIO()
    Image.new("RGBA", (16, 16), color).save(buf, format="PNG")
    return buf.getvalue()


def _jar(path, files):
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)


def _client_jar(tmp_path, version):
    path = itemicons.JAR_DIR / f"{itemicons._safe_name(version)}.jar"
    _jar(path, {
        "assets/minecraft/textures/item/diamond_pickaxe.png": PNG,
        "assets/minecraft/textures/block/stone.png": _png_bytes(),
        "assets/minecraft/models/block/stone.json": BLOCK_MODEL,
        "assets/minecraft/textures/block/grass_block_top.png": PNG,
        "assets/minecraft/textures/block/cobblestone.png": PNG,
        "assets/minecraft/textures/block/cherry_planks.png": PNG,
        "assets/minecraft/textures/item/zombie_spawn_egg.png": PNG,
    })
    return path


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    """Keep the real cache untouched; use a temporary icon root and fake Mojang metadata."""
    monkeypatch.setattr(itemicons, "ROOT", tmp_path / "icons")
    monkeypatch.setattr(itemicons, "PNG_DIR", tmp_path / "icons" / "png")
    monkeypatch.setattr(itemicons, "INDEX_DIR", tmp_path / "icons" / "index")
    monkeypatch.setattr(itemicons, "JAR_DIR", tmp_path / "icons" / "client")
    monkeypatch.setattr(itemicons, "ASSETS_DIR", tmp_path / "icons" / "assets")
    monkeypatch.setattr(itemicons, "OVERLAY_DIR", tmp_path / "icons" / "overlays")
    monkeypatch.setattr(itemicons, "BLOCKS_DIR", tmp_path / "icons" / "blocks")
    # Batch rendering is exercised explicitly in the tests below; don't spawn background tasks.
    monkeypatch.setattr(itemicons, "schedule_batch", lambda *args, **kwargs: None)
    itemicons._LOCKS.clear()
    itemicons._RENDER_LOCKS.clear()
    itemicons._RENDERERS.clear()
    itemicons._BATCH_TASKS.clear()
    inst = _inst(tmp_path)

    async def fake_version(version):
        return {"downloads": {"client": {"url": "https://example.invalid/client.jar"}}}

    async def fake_download(url, dest, **kwargs):
        dest.write_bytes(PNG)

    monkeypatch.setattr(itemicons.providers, "mojang_version", fake_version)
    monkeypatch.setattr(itemicons.net, "download", fake_download)
    yield inst


def test_icons_are_extracted_from_vanilla_client_jar(_isolate):
    inst = _isolate
    _client_jar(inst.dir, inst.cfg.mc_version)  # present in temp JAR_DIR already
    icons = asyncio.run(itemicons.prepare(inst))

    assert icons["minecraft:diamond_pickaxe"].endswith(".png")
    assert itemicons.icon_file(inst, "minecraft:stone") is not None
    assert itemicons.icon_file(inst, "minecraft:grass_block") is not None  # _top fallback
    # Derived blocks reuse their base material's texture
    assert itemicons.icon_file(inst, "minecraft:stone_slab") is not None
    assert itemicons.icon_file(inst, "minecraft:cobblestone_slab") is not None
    assert itemicons.icon_file(inst, "minecraft:cherry_fence") is not None
    # Entity ids resolve to their spawn egg
    assert itemicons.icon_file(inst, "minecraft:zombie") is not None
    assert itemicons.icon_file(inst, "minecraft:unknown") is None


def test_mod_and_resource_pack_textures_override_vanilla(_isolate):
    inst = _isolate
    _client_jar(inst.dir, inst.cfg.mc_version)
    _jar(inst.dir / "mods" / "create.jar", {
        "assets/create/textures/item/andesite_alloy.png": PNG,
    })
    _jar(inst.dir / ".minenode" / "resourcepacks" / "pack.zip", {
        "assets/minecraft/textures/block/stone.png": PNG,
    })

    icons = asyncio.run(itemicons.prepare(inst))

    assert "create:andesite_alloy" in icons
    assert "minecraft:stone" in icons


def test_index_is_rebuilt_when_mods_change(_isolate):
    inst = _isolate
    _client_jar(inst.dir, inst.cfg.mc_version)
    before = asyncio.run(itemicons.prepare(inst))
    assert "create:andesite_alloy" not in before

    _jar(inst.dir / "mods" / "create.jar", {
        "assets/create/textures/item/andesite_alloy.png": PNG,
    })
    after = asyncio.run(itemicons.prepare(inst))
    assert "create:andesite_alloy" in after


def test_icon_file_rejects_malformed_ids(_isolate):
    inst = _isolate
    asyncio.run(itemicons.prepare(inst))
    assert itemicons.icon_file(inst, "") is None
    assert itemicons.icon_file(inst, "x" * 200) is None


def test_blocks_are_rendered_and_cached(_isolate):
    inst = _isolate
    _client_jar(inst.dir, inst.cfg.mc_version)
    asyncio.run(itemicons.prepare(inst))

    first = itemicons.icon_file(inst, "minecraft:stone")
    assert first is not None
    assert itemicons.BLOCKS_DIR in first.parents
    assert Image.open(first).size[0] > 0
    # A second lookup must hit the cache and return exactly the same file.
    assert itemicons.icon_file(inst, "minecraft:stone") == first

    calls = []
    original = itemicons.blockrender.MinecraftBlockRenderer.render

    def counting(self, *args, **kwargs):
        calls.append(1)
        return original(self, *args, **kwargs)

    itemicons.blockrender.MinecraftBlockRenderer.render = counting
    try:
        assert itemicons.icon_file(inst, "minecraft:stone") == first
        assert calls == []
    finally:
        itemicons.blockrender.MinecraftBlockRenderer.render = original


def test_items_without_a_block_model_fall_back_to_flat_texture(_isolate):
    inst = _isolate
    _client_jar(inst.dir, inst.cfg.mc_version)
    asyncio.run(itemicons.prepare(inst))

    path = itemicons.icon_file(inst, "minecraft:diamond_pickaxe")
    assert path is not None
    assert itemicons.PNG_DIR in path.parents


def test_cached_index_still_extracts_assets_on_upgrade(_isolate):
    inst = _isolate
    _client_jar(inst.dir, inst.cfg.mc_version)
    asyncio.run(itemicons.prepare(inst))

    # Simulate upgrading from the flat-icon version: index exists, models were never extracted.
    shutil.rmtree(itemicons.ASSETS_DIR)
    shutil.rmtree(itemicons.OVERLAY_DIR)
    itemicons._RENDERERS.clear()

    asyncio.run(itemicons.prepare(inst))
    path = itemicons.icon_file(inst, "minecraft:stone")
    assert path is not None
    assert itemicons.BLOCKS_DIR in path.parents


def test_resource_pack_override_invalidates_render_cache(_isolate):
    inst = _isolate
    _client_jar(inst.dir, inst.cfg.mc_version)
    asyncio.run(itemicons.prepare(inst))
    first = itemicons.icon_file(inst, "minecraft:stone")

    _jar(inst.dir / ".minenode" / "resourcepacks" / "pack.zip", {
        "assets/minecraft/textures/block/stone.png": _png_bytes((255, 0, 0, 255)),
    })
    asyncio.run(itemicons.prepare(inst))  # signature changed -> rebuild
    second = itemicons.icon_file(inst, "minecraft:stone")

    assert second is not None and second != first


def test_batch_renders_all_models_once_and_records_missing(_isolate):
    inst = _isolate
    _jar(itemicons.JAR_DIR / f"{itemicons._safe_name(inst.cfg.mc_version)}.jar", {
        "assets/minecraft/textures/block/stone.png": _png_bytes(),
        "assets/minecraft/models/block/stone.json": BLOCK_MODEL,
        "assets/minecraft/textures/block/dirt.png": _png_bytes((90, 70, 50, 255)),
        "assets/minecraft/models/block/dirt.json": BLOCK_MODEL.replace("stone", "dirt"),
        "assets/minecraft/models/item/diamond_pickaxe.json": json.dumps({
            "parent": "minecraft:item/generated",
            "textures": {"layer0": "minecraft:item/diamond_pickaxe"},
        }),
    })
    asyncio.run(itemicons.prepare(inst))

    key = itemicons._assets_key(itemicons.signature(inst))
    itemicons._build_all(inst, key)
    cache = itemicons._block_cache(key)

    manifest = json.loads((cache / "manifest.json").read_text(encoding="utf-8"))
    assert {"minecraft:stone", "minecraft:dirt"} <= set(manifest)
    assert len(list((cache / "png").glob("*.png"))) == 2

    # An id with no block model is persisted in the negative cache and answered immediately.
    assert itemicons.rendered_file(inst, "minecraft:unobtainium") is None
    missing = json.loads((cache / "missing.json").read_text(encoding="utf-8"))
    assert "minecraft:unobtainium" in missing
    assert "minecraft:diamond_pickaxe" in missing  # collected in the same single pass
    assert itemicons.rendered_file(inst, "minecraft:unobtainium") is None


def test_prepare_schedules_batch_that_fills_the_global_cache(_isolate, monkeypatch):
    monkeypatch.setattr(itemicons, "schedule_batch", REAL_SCHEDULE_BATCH)
    inst = _isolate
    _client_jar(inst.dir, inst.cfg.mc_version)

    async def run():
        await itemicons.prepare(inst)
        key = itemicons._assets_key(itemicons.signature(inst))
        task = itemicons._BATCH_TASKS.get(key)
        assert task is not None
        await task
        return key

    key = asyncio.run(run())
    cache = itemicons._block_cache(key)
    assert len(list((cache / "png").glob("*.png"))) >= 1
    assert (cache / "manifest.json").is_file()


def test_render_cache_is_shared_between_servers(_isolate):
    inst = _isolate
    _client_jar(inst.dir, inst.cfg.mc_version)
    asyncio.run(itemicons.prepare(inst))
    first = itemicons.icon_file(inst, "minecraft:stone")

    other_dir = inst.dir.parent / "other-server"
    other = _inst(other_dir)
    other.cfg.id = "other"
    assert itemicons.icon_file(other, "minecraft:stone") == first
