import json

from PIL import Image

from app.blockrender import MinecraftBlockRenderer

MODEL = {
    "textures": {"all": "minecraft:block/test_block"},
    "elements": [{
        "from": [0, 0, 0],
        "to": [16, 16, 16],
        "faces": {
            face: {"texture": "#all", "uv": [0, 0, 16, 16]}
            for face in ("up", "down", "north", "south", "east", "west")
        },
    }],
}


def _assets(root, color=(180, 150, 120, 255), *, model=MODEL):
    (root / "minecraft" / "models" / "block").mkdir(parents=True, exist_ok=True)
    (root / "minecraft" / "textures" / "block").mkdir(parents=True, exist_ok=True)
    (root / "minecraft" / "models" / "block" / "test_block.json").write_text(
        json.dumps(model), encoding="utf-8"
    )
    Image.new("RGBA", (16, 16), color).save(
        root / "minecraft" / "textures" / "block" / "test_block.png"
    )
    return root


def test_renders_shaded_isometric_cube(tmp_path):
    renderer = MinecraftBlockRenderer(_assets(tmp_path / "assets"))
    img = renderer.render("minecraft:block/test_block", size=128)

    assert img.size == (128, 128)
    assert img.getchannel("A").getbbox() is not None  # something was actually drawn
    # Top face is the brightest one; with a uniform texture its pixels keep the source colour.
    assert img.getpixel((64, 64))[3] > 0


def test_resolves_parent_chain_and_tint(tmp_path):
    root = _assets(tmp_path / "assets")
    (root / "minecraft" / "models" / "block" / "tinted.json").write_text(json.dumps({
        "parent": "minecraft:block/test_block",
        "textures": {"all": "#base"},
    }), encoding="utf-8")
    renderer = MinecraftBlockRenderer(root)

    plain = renderer.render("minecraft:block/test_block", size=64)
    tinted = renderer.render("minecraft:block/tinted", size=64, tint_rgb=(255, 0, 0))
    assert plain.size == tinted.size == (64, 64)
    assert tinted.getchannel("A").getbbox() is not None


def test_later_asset_root_overrides_textures(tmp_path):
    base = _assets(tmp_path / "base")
    override = _assets(tmp_path / "override", color=(255, 0, 0, 255))

    renderer = MinecraftBlockRenderer([base, override])
    pixel = renderer.get_texture("minecraft:block/test_block").getpixel((0, 0))
    assert pixel == (255, 0, 0, 255)


def test_missing_texture_does_not_crash(tmp_path):
    root = _assets(tmp_path / "assets")
    (root / "minecraft" / "models" / "block" / "test_block.json").write_text(json.dumps({
        "textures": {"all": "minecraft:block/does_not_exist"},
        "elements": MODEL["elements"],
    }), encoding="utf-8")
    renderer = MinecraftBlockRenderer(root)

    assert renderer.get_texture("minecraft:block/does_not_exist").size == (16, 16)
    assert renderer.render("minecraft:block/test_block", size=64).size == (64, 64)
