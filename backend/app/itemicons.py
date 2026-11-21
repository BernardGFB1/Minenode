"""Item and block icons for the public player-statistics page.

Icons for the public player-statistics page. Blocks are rendered from their JSON models into a
shaded isometric PNG (see :mod:`app.blockrender`); items without a block model fall back to their
flat texture sprite. Assets are taken from (in increasing priority):

* the vanilla ``client.jar`` for the server's Minecraft version (resolved through
  ``providers.mojang_version`` / Mojang's version manifest),
* every enabled mod/plugin JAR in the server's content folder,
* the server's locally installed resource pack(s), which override vanilla/mod assets.

All data is cached in ``CACHE_DIR/itemicons``. Rendered block icons live in a single global cache
shared by every server with the same asset set (Minecraft version + mods + resource packs), so a
visitor never pays for rendering that somebody else has already done. When a render is missing the
whole model inventory is rendered in one background pass (each jar is unpacked only once), and
blocks with no texture are recorded in a ``missing.json`` negative-cache file.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import re
import shutil
import threading
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PIL import Image

from . import blockrender, net, providers
from .config import CACHE_DIR
from .content import content_dir

if TYPE_CHECKING:
    from .manager import ServerInstance

ROOT = CACHE_DIR / "itemicons"
PNG_DIR = ROOT / "png"
INDEX_DIR = ROOT / "index"
JAR_DIR = ROOT / "client"
ASSETS_DIR = ROOT / "assets"        # vanilla models/textures, shared per MC version
OVERLAY_DIR = ROOT / "overlays"     # mod + resource-pack models/textures, per server
BLOCKS_DIR = ROOT / "blocks"        # finished isometric PNGs, shared by ALL servers/visitors

# Isometric camera and output size used for the cached block icons.
RENDER_SIZE = 128
ROT_Y, ROT_X = -45.0, 30.0
# Default biome colours for the few blocks whose textures are tinted by Minecraft.
_TINTS: dict[str, tuple[int, int, int]] = {
    "grass_block": (124, 189, 107),
    "water": (63, 116, 204),
    "oak_leaves": (83, 183, 63),
    "spruce_leaves": (48, 112, 62),
    "birch_leaves": (92, 174, 67),
    "jungle_leaves": (68, 152, 49),
    "acacia_leaves": (96, 155, 50),
    "dark_oak_leaves": (49, 127, 47),
    "cherry_leaves": (255, 174, 201),
    "mangrove_leaves": (68, 156, 68),
}

_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")
_LOCKS: dict[str, asyncio.Lock] = {}
_RENDER_LOCKS: dict[str, threading.Lock] = {}
_RENDERERS: dict[tuple[str, ...], blockrender.MinecraftBlockRenderer] = {}
_BATCH_TASKS: dict[str, asyncio.Task] = {}
_STATE_LOCK = threading.Lock()  # guards access to the shared batch task registry


def _safe_name(value: str) -> str:
    return _SAFE.sub("_", str(value))[:120] or "unknown"


def _sha1(path: Path) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _lock_for(inst: "ServerInstance") -> asyncio.Lock:
    sid = _safe_name(getattr(inst.cfg, "id", "unknown"))
    return _LOCKS.setdefault(sid, asyncio.Lock())


def _mod_jars(inst: "ServerInstance") -> list[Path]:
    folder = content_dir(inst)
    try:
        return sorted(p for p in folder.glob("*.jar") if p.is_file())
    except OSError:
        return []


def _resource_packs(inst: "ServerInstance") -> list[Path]:
    """Locally installed resource-pack ZIPs (their textures override vanilla/mod icons)."""
    folder = Path(inst.dir) / ".minenode" / "resourcepacks"
    try:
        return sorted(p for p in folder.glob("*.zip") if p.is_file())
    except OSError:
        return []


def signature(inst: "ServerInstance") -> Any:
    mods = []
    for path in _mod_jars(inst):
        try:
            st = path.stat()
            mods.append((path.name, st.st_mtime_ns, st.st_size))
        except OSError:
            continue
    packs = []
    for path in _resource_packs(inst):
        try:
            st = path.stat()
            packs.append((str(path), st.st_mtime_ns, st.st_size))
        except OSError:
            continue
    return [getattr(inst.cfg, "mc_version", ""), mods, packs]


def _store_png(data: bytes) -> str | None:
    key = hashlib.sha1(data).hexdigest()[:32]
    PNG_DIR.mkdir(parents=True, exist_ok=True)
    path = PNG_DIR / f"{key}.png"
    if not path.is_file():
        try:
            path.write_bytes(data)
        except OSError:
            return None
    return f"png/{key}.png"


def _collect(jar: Path, icons: dict[str, str]) -> None:
    """Register every item/block texture from *jar*, keyed by ``<namespace>:<name>``.

    Both the file stem and its full relative path are registered, so nested mod textures and
    vanilla block textures (e.g. ``block/stone.png`` for ``minecraft:stone``) resolve correctly.
    """
    try:
        with zipfile.ZipFile(jar) as zf:
            for info in zf.infolist():
                name = info.filename
                if not name.lower().endswith(".png") or info.is_dir():
                    continue
                parts = name.split("/")
                if len(parts) < 5 or parts[0] != "assets" or parts[2] != "textures":
                    continue
                if parts[3] not in ("item", "block"):
                    continue
                namespace = parts[1]
                rel = "/".join(parts[4:])[:-4]  # drop .png
                stem = rel.rsplit("/", 1)[-1]
                try:
                    data = zf.read(info)
                except (OSError, zipfile.BadZipFile, RuntimeError):
                    continue
                stored = _store_png(data)
                if not stored:
                    continue
                icons.setdefault(f"{namespace}:{stem}", stored)
                icons.setdefault(f"{namespace}:{rel}", stored)
                if namespace == "minecraft":
                    icons.setdefault(stem, stored)
    except (OSError, zipfile.BadZipFile, ValueError):
        return


def _extract_assets(source: Path, dest: Path, *, overwrite: bool = False) -> None:
    """Extract model JSONs and textures from a jar/zip into a renderer-compatible root.

    Only ``assets/<ns>/models/*.json`` and ``assets/<ns>/textures/*.png`` are kept. Zip-slip paths
    are rejected before anything is written.
    """
    try:
        with zipfile.ZipFile(source) as zf:
            root = dest.resolve()
            for info in zf.infolist():
                name = info.filename.replace("\\", "/")
                if info.is_dir():
                    continue
                is_model = name.endswith(".json") and "/models/" in name
                is_texture = name.endswith(".png") and "/textures/" in name
                if not (is_model or is_texture):
                    continue
                parts = name.split("/")
                if len(parts) < 4 or parts[0] != "assets":
                    continue
                target = dest.joinpath(*parts[1:])  # strip the "assets/" prefix
                try:
                    resolved = target.resolve()
                except OSError:
                    continue
                if not resolved.is_relative_to(root):
                    continue
                if target.is_file() and not overwrite:
                    continue
                try:
                    data = zf.read(info)
                except (OSError, zipfile.BadZipFile, RuntimeError):
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                try:
                    target.write_bytes(data)
                except OSError:
                    continue
    except (OSError, zipfile.BadZipFile, ValueError):
        return


def _text(path: Path, value: str | None = None) -> str | None:
    try:
        if value is None:
            return path.read_text(encoding="utf-8")
        path.write_text(value, encoding="utf-8")
    except OSError:
        pass
    return None


def _prepare_assets(inst: "ServerInstance", jar: Path | None, version: str, *, rebuild: bool) -> None:
    """Keep vanilla assets (shared per version) and the per-server override layer in sync."""
    if jar and jar.is_file():
        vanilla = ASSETS_DIR / _safe_name(version)
        try:
            st = jar.stat()
            jar_sig = f"{jar.name}:{st.st_mtime_ns}:{st.st_size}"
        except OSError:
            jar_sig = ""
        marker = vanilla / ".extracted"
        if rebuild or jar_sig != _text(marker):
            _extract_assets(jar, vanilla, overwrite=True)
            _text(marker, jar_sig)

    sid = _safe_name(getattr(inst.cfg, "id", "unknown"))
    overlay = OVERLAY_DIR / sid
    if rebuild:
        shutil.rmtree(overlay, ignore_errors=True)
    if not overlay.is_dir():
        overlay.mkdir(parents=True, exist_ok=True)
        for mod in _mod_jars(inst):
            _extract_assets(mod, overlay, overwrite=True)
        for pack in _resource_packs(inst):
            _extract_assets(pack, overlay, overwrite=True)  # resource packs win
        _text(overlay / ".extracted", "ok")


def _assets_ready(inst: "ServerInstance", version: str, jar: Path | None) -> bool:
    """True when the model/texture roots needed by the renderer are already extracted."""
    vanilla = ASSETS_DIR / _safe_name(version)
    marker = vanilla / ".extracted"
    ready = marker.is_file()
    if jar and jar.is_file():
        try:
            st = jar.stat()
            ready = ready and _text(marker) == f"{jar.name}:{st.st_mtime_ns}:{st.st_size}"
        except OSError:
            pass
    return ready and (OVERLAY_DIR / _safe_name(getattr(inst.cfg, "id", "unknown")) / ".extracted").is_file()


def _index_path(inst: "ServerInstance") -> Path:
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    return INDEX_DIR / f"{_safe_name(getattr(inst.cfg, 'id', 'unknown'))}.json"


def _load_index(inst: "ServerInstance") -> tuple[Any, dict[str, str]]:
    try:
        data = json.loads(_index_path(inst).read_text(encoding="utf-8"))
        return data.get("signature"), data.get("icons", {})
    except (OSError, ValueError):
        return None, {}


def _build(inst: "ServerInstance", jar: Path | None, sig: Any) -> dict[str, str]:
    icons: dict[str, str] = {}
    if jar and jar.is_file():
        _collect(jar, icons)
    for mod in _mod_jars(inst):
        _collect(mod, icons)
    for pack in _resource_packs(inst):
        _collect(pack, icons)  # resource packs have the final say
    try:
        _index_path(inst).write_text(json.dumps({"signature": sig, "icons": icons}), encoding="utf-8")
    except OSError:
        pass
    return icons


def _assets_key(sig: Any) -> str:
    """Stable id for an asset set – identical for every server sharing version/mods/pack."""
    return hashlib.sha1(json.dumps(sig, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]


def _block_cache(key: str) -> Path:
    return BLOCKS_DIR / key


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".part")
    try:
        tmp.write_text(json.dumps(value), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)


def _load_missing(cache: Path) -> set[str]:
    data = _read_json(cache / "missing.json", [])
    return set(data) if isinstance(data, list) and all(isinstance(x, str) for x in data) else set()


def _mark_missing(cache: Path, item_id: str) -> None:
    """Negative cache: remember that this id has no renderable block model/texture."""
    with _STATE_LOCK:
        missing = _load_missing(cache)
        if item_id in missing:
            return
        missing.add(item_id)
        _write_json(cache / "missing.json", sorted(missing))


def _render_path(cache: Path, model_loc: str, tint) -> Path:
    digest = hashlib.sha1(json.dumps(
        [model_loc, RENDER_SIZE, ROT_Y, ROT_X, tint], sort_keys=True
    ).encode("utf-8")).hexdigest()[:24]
    return cache / "png" / f"{digest}.png"


def _render_one(renderer: blockrender.MinecraftBlockRenderer, model_loc: str, tint, path: Path) -> bool:
    """Render a single block into the shared cache (safe when called from many threads)."""
    lock = _RENDER_LOCKS.setdefault(str(path), threading.Lock())
    with lock:
        if path.is_file():
            return True
        tmp = path.with_suffix(".part")
        try:
            img = renderer.render(
                model_loc, size=RENDER_SIZE, rot_y=ROT_Y, rot_x=ROT_X, tint_rgb=tint
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            _crop_square(img).save(tmp, format="PNG")
            os.replace(tmp, path)
            return True
        except Exception:
            with contextlib.suppress(OSError):
                tmp.unlink(missing_ok=True)
            return False


def _discover_blocks(renderer: blockrender.MinecraftBlockRenderer) -> tuple[dict[str, str], set[str]]:
    """Walk the (already unpacked) asset roots once.

    Returns ``({item_id: model_loc} for every block, ids of items without a block model)``. The
    second set goes straight into the negative cache so flat sprites never trigger lookups again.
    """
    found: dict[str, str] = {}
    flat: set[str] = set()
    for root in reversed(renderer.assets_dirs):  # later roots override earlier ones
        try:
            files = sorted(root.glob("*/models/**/*.json"))
        except OSError:
            continue
        for path in files:
            try:
                rel = path.relative_to(root).parts
            except ValueError:
                continue
            if len(rel) < 4 or rel[1] != "models":
                continue
            ns, stem = rel[0], path.stem
            item_id = f"{ns}:{stem}"
            model_path = "/".join(rel[2:])[:-5]  # drop ".json"
            if rel[2] == "block":
                found[item_id] = f"{ns}:{model_path}"
            elif rel[2] == "item":
                # Some blocks (e.g. doors) are drawn by a model referenced from the item model.
                data = _read_json(path, {})
                parent = data.get("parent") if isinstance(data, dict) else None
                if isinstance(parent, str) and (parent.startswith("block/") or ":block/" in parent):
                    found[item_id] = parent if ":" in parent else f"{ns}:{parent}"
                else:
                    flat.add(item_id)
    flat.difference_update(found)  # never negative-cache an id that does have a block model
    return found, flat


def _build_all(inst: "ServerInstance", key: str) -> None:
    """Render every missing block for an asset set in ONE pass (no repeated jar unpacking)."""
    cache = _block_cache(key)
    cache.mkdir(parents=True, exist_ok=True)
    renderer = _renderer_for(inst)
    if renderer is None:
        return
    manifest = cache / "manifest.json"
    flat_path = cache / "flat.json"
    blocks = _read_json(manifest, None)
    flat_data = _read_json(flat_path, None)
    if not isinstance(blocks, dict) or not blocks or not isinstance(flat_data, list):
        blocks, flat = _discover_blocks(renderer)
        _write_json(manifest, blocks)
        _write_json(flat_path, sorted(flat))
    else:
        flat = set(flat_data)

    missing = _load_missing(cache)
    failed: list[str] = []
    for item_id, model_loc in blocks.items():
        if item_id in missing:
            continue
        tint = _tint_for(item_id.rsplit(":", 1)[-1])
        path = _render_path(cache, model_loc, tint)
        if path.is_file():
            continue
        if not _render_one(renderer, model_loc, tint, path):
            failed.append(item_id)
    if failed or flat:
        with _STATE_LOCK:
            current = _load_missing(cache)
            current.update(flat)
            current.update(failed)
            _write_json(cache / "missing.json", sorted(current))


def schedule_batch(inst: "ServerInstance", key: str | None = None) -> None:
    """Start once per asset set a background task that fills the shared render cache."""
    key = key or _assets_key(signature(inst))
    with _STATE_LOCK:
        task = _BATCH_TASKS.get(key)
        if task is not None and not task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # called from a worker thread – prepare() already scheduled the batch

        def _clear(finished: asyncio.Task) -> None:
            with contextlib.suppress(Exception):
                finished.result()

        task = loop.create_task(asyncio.to_thread(_build_all, inst, key))
        task.add_done_callback(_clear)
        _BATCH_TASKS[key] = task


async def prepare(inst: "ServerInstance", *, force: bool = False) -> dict[str, str]:
    """Ensure the icon index and the renderer's asset roots match the server's current state."""
    sig = signature(inst)
    key = _assets_key(sig)
    version = str(getattr(inst.cfg, "mc_version", "") or "")
    jar_hint = JAR_DIR / f"{_safe_name(version)}.jar" if version else None
    cached_sig, cached_icons = _load_index(inst)
    if not force and cached_sig == sig and cached_icons and _assets_ready(inst, version, jar_hint):
        schedule_batch(inst, key)
        return cached_icons
    async with _lock_for(inst):
        cached_sig, cached_icons = _load_index(inst)
        if not force and cached_sig == sig and cached_icons and _assets_ready(inst, version, jar_hint):
            schedule_batch(inst, key)
            return cached_icons

        jar: Path | None = None
        try:
            meta = await providers.mojang_version(version)
        except Exception:
            meta = None
        download = (meta or {}).get("downloads", {}).get("client") or {}
        url, sha = download.get("url"), download.get("sha1")
        if url:
            JAR_DIR.mkdir(parents=True, exist_ok=True)
            jar = JAR_DIR / f"{_safe_name(version)}.jar"
            if not jar.is_file() or (sha and _sha1(jar) != sha):
                try:
                    await net.download(url, jar, sha1=sha)
                except Exception:
                    jar = None
        elif jar_hint and jar_hint.is_file():
            # Existing installation with a cached index: reuse the JAR we already downloaded.
            jar = jar_hint

        def finish() -> dict[str, str]:
            icons = _build(inst, jar, sig)
            _prepare_assets(inst, jar, version, rebuild=True)
            # Renderers hold texture images in memory; drop them after the assets changed.
            _RENDERERS.clear()
            return icons

        icons = await asyncio.to_thread(finish)
        schedule_batch(inst, key)  # assets are ready – fill the shared cache in the background
        return icons


# Blocks such as slabs, fences and pressure plates have no texture of their own – they render
# the texture of the material they are made from. The aliases below point those ids at the
# base block texture (e.g. stone_slab -> stone, cherry_fence -> cherry_planks).
_SHAPES = (
    "_pressure_plate", "_fence_gate", "_hanging_sign",
    "_slab", "_stairs", "_wall", "_button", "_fence", "_trapdoor", "_door", "_sign",
)
_PLURALS = {
    "brick": "bricks",
    "stone_brick": "stone_bricks",
    "nether_brick": "nether_bricks",
    "red_nether_brick": "red_nether_bricks",
    "mud_brick": "mud_bricks",
    "prismarine_brick": "prismarine_bricks",
    "quartz": "quartz_block",
}
_TEXTURE_VARIANTS = ("_top", "_side", "_bottom", "_item")


def _candidates(item_id: str):
    """Ordered id -> texture keys to try, from the exact id to derived/base textures."""
    ns, _, name = item_id.partition(":")
    if not name:
        ns, name = "minecraft", ns
    names = [name]
    names += [f"{name}{suffix}" for suffix in _TEXTURE_VARIANTS]
    names.append(f"{name}_spawn_egg")  # entity ids -> mob spawn egg
    for shape in _SHAPES:
        if not name.endswith(shape) or len(name) == len(shape):
            continue
        base = name[: -len(shape)]
        names += [base, f"{base}_planks", f"{base}_block", f"{base}_bricks", f"{base}s"]
        if base in _PLURALS:
            names.append(_PLURALS[base])
    seen: set[str] = set()
    out: list[str] = []
    for value in names:
        if not value:
            continue
        for key in (f"{ns}:{value}", value):
            if key not in seen:
                seen.add(key)
                out.append(key)
    return out


def _renderer_for(inst: "ServerInstance") -> blockrender.MinecraftBlockRenderer | None:
    version = str(getattr(inst.cfg, "mc_version", "") or "")
    roots = [
        ASSETS_DIR / _safe_name(version),
        OVERLAY_DIR / _safe_name(getattr(inst.cfg, "id", "unknown")),
    ]
    roots = [p for p in roots if p.is_dir()]
    if not roots:
        return None
    key = tuple(str(p) for p in roots)
    renderer = _RENDERERS.get(key)
    if renderer is None:
        renderer = blockrender.MinecraftBlockRenderer(roots)
        _RENDERERS[key] = renderer
    return renderer


def _model_loc_for(renderer: blockrender.MinecraftBlockRenderer, item_id: str) -> tuple[str | None, str]:
    """Find the block model that describes *item_id* (or a parent of its item model)."""
    ns, _, name = item_id.partition(":")
    if not name:
        ns, name = "minecraft", ns
    block = f"{ns}:block/{name}"
    if renderer.has_model(block):
        return block, name
    try:
        item_model = renderer.load_model_json(f"{ns}:item/{name}")
    except (FileNotFoundError, ValueError, OSError):
        return None, name
    parent = item_model.get("parent")
    if isinstance(parent, str) and (parent.startswith("block/") or ":block/" in parent):
        return (parent if ":" in parent else f"{ns}:{parent}"), name
    return None, name


def _tint_for(name: str) -> tuple[int, int, int] | None:
    if name.endswith("_leaves"):
        return _TINTS.get(name) or (83, 183, 63)
    return _TINTS.get(name)


def _crop_square(img: Image.Image) -> Image.Image:
    """Trim transparent margins and centre the block in a square icon."""
    bbox = img.getchannel("A").getbbox()
    if not bbox:
        return img
    img = img.crop(bbox)
    w, h = img.size
    side = max(w, h)
    if side == w == h:
        return img
    out = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    out.alpha_composite(img, ((side - w) // 2, (side - h) // 2))
    return out


def rendered_file(inst: "ServerInstance", item_id: str) -> Path | None:
    """Return a shared-cache isometric PNG; render on demand until the batch pass catches up."""
    item_id = str(item_id or "").strip()
    if not item_id or len(item_id) > 128:
        return None
    renderer = _renderer_for(inst)
    if renderer is None:
        return None
    cache = _block_cache(_assets_key(signature(inst)))
    if item_id in _load_missing(cache):
        return None  # negative cache – answer immediately, don't retry the jar
    model_loc, name = _model_loc_for(renderer, item_id)
    if not model_loc:
        _mark_missing(cache, item_id)
        return None
    tint = _tint_for(name)
    path = _render_path(cache, model_loc, tint)
    if not path.is_file() and not _render_one(renderer, model_loc, tint, path):
        _mark_missing(cache, item_id)
        return None
    return path


def icon_file(inst: "ServerInstance", item_id: str) -> Path | None:
    """Resolve an item id to an icon (rendered block PNG, or a flat texture fallback)."""
    item_id = str(item_id or "").strip()
    if not item_id or len(item_id) > 128:
        return None
    rendered = rendered_file(inst, item_id)
    if rendered:
        return rendered
    _, icons = _load_index(inst)
    if not icons:
        return None
    for key in _candidates(item_id):
        rel = icons.get(key)
        if not rel or not re.match(r"^png/[A-Za-z0-9]+\.png$", rel):
            continue
        path = ROOT / rel
        if path.is_file():
            return path
    return None
