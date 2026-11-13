"""Custom world generation chosen while creating a server.

Files are uploaded first (``stage``), inspected and kept in a temporary area. When the server is created
they are moved into place *before the first start*, so the world is generated with them from the start:

* datapacks  -> ``<level-name>/datapacks/``  (every engine)
* plugins    -> ``plugins/`` and ``bukkit.yml`` ``worlds.<level>.generator`` (Paper, Purpur)
* mods       -> ``mods/`` (Fabric, Quilt, Forge, NeoForge)

``level-type`` and ``level-seed`` go to ``server.properties``.
"""
from __future__ import annotations

import json
import re
import secrets
import shutil
import time
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING

from .config import TMP_DIR
from .properties import read_properties, update_properties

if TYPE_CHECKING:
    from .manager import ServerInstance

STAGE_DIR = TMP_DIR / "worldgen"
STAGE_TTL = 24 * 3600
MAX_FILE = 512 * 1024 * 1024
MAX_FILES = 20

# What each engine can load.
ENGINE_KINDS: dict[str, set[str]] = {
    "vanilla": {"datapack"},
    "paper": {"datapack", "plugin"},
    "purpur": {"datapack", "plugin"},
    "fabric": {"datapack", "mod"},
    "quilt": {"datapack", "mod"},
    "forge": {"datapack", "mod"},
    "neoforge": {"datapack", "mod"},
    "custom": {"datapack", "plugin", "mod"},
}
# Mod loaders an engine accepts (Quilt runs Fabric mods; NeoForge 1.20.1 still reads Forge's mods.toml).
ENGINE_LOADERS: dict[str, set[str]] = {
    "fabric": {"fabric"},
    "quilt": {"quilt", "fabric"},
    "forge": {"forge"},
    "neoforge": {"neoforge", "forge"},
    "custom": {"fabric", "quilt", "forge", "neoforge"},
}
KIND_LABEL = {"datapack": "datapack", "plugin": "plugin", "mod": "mod"}

LEVEL_TYPES = {  # preset -> (1.19+ name, older name)
    "normal": ("minecraft:normal", "default"),
    "flat": ("minecraft:flat", "flat"),
    "large_biomes": ("minecraft:large_biomes", "largeBiomes"),
    "amplified": ("minecraft:amplified", "amplified"),
}
CUSTOM_LEVEL_TYPE = re.compile(r"^[a-z0-9_.-]+(:[a-z0-9_./-]+)?$")
TOKEN = re.compile(r"^[0-9a-f]{24}$")
SAFE_NAME = re.compile(r"[^A-Za-z0-9._+-]+")


class WorldgenError(Exception):
    pass


# ---------------------------------------------------------------- inspection
def _plugin_name(text: str) -> str | None:
    m = re.search(r"^name:\s*['\"]?([^'\"\r\n#]+?)['\"]?\s*$", text, re.M)
    return m.group(1).strip() if m else None


def _strip_wrapper(path: Path, names: list[str]) -> bool:
    """A datapack zipped together with its folder: re-zip it with pack.mcmeta at the root."""
    candidates = [n for n in names if n.endswith("/pack.mcmeta") and n.count("/") == 1]
    if not candidates:
        return False
    prefix = candidates[0][: -len("pack.mcmeta")]
    fixed = path.with_name(path.name + ".fixed")
    with zipfile.ZipFile(path) as zf, zipfile.ZipFile(fixed, "w", zipfile.ZIP_DEFLATED) as out:
        for info in zf.infolist():
            if info.filename.startswith(prefix) and info.filename != prefix:
                out.writestr(info.filename[len(prefix):], zf.read(info))
    fixed.replace(path)
    return True


def inspect(path: Path) -> dict:
    """Identify a datapack (.zip), plugin or mod (.jar). Raises WorldgenError for anything else."""
    try:
        zf = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise WorldgenError("To nie jest plik .zip ani .jar") from exc
    with zf:
        names = zf.namelist()
        name_set = set(names)
        for descriptor in ("paper-plugin.yml", "plugin.yml"):
            if descriptor in name_set:
                text = zf.read(descriptor).decode("utf-8", "replace")
                return {"kind": "plugin", "name": _plugin_name(text), "loaders": []}
        loaders = []
        if "fabric.mod.json" in name_set:
            loaders.append("fabric")
        if "quilt.mod.json" in name_set:
            loaders.append("quilt")
        if "META-INF/neoforge.mods.toml" in name_set:
            loaders.append("neoforge")
        if "META-INF/mods.toml" in name_set:
            loaders.append("forge")
        if loaders:
            name = None
            if "fabric.mod.json" in name_set:
                try:
                    name = json.loads(zf.read("fabric.mod.json").decode("utf-8", "replace")).get("name")
                except (ValueError, AttributeError):
                    pass
            return {"kind": "mod", "name": name, "loaders": loaders}
        wrapped = "pack.mcmeta" not in name_set
    if wrapped:
        if not _strip_wrapper(path, names):
            raise WorldgenError("Nie rozpoznano pliku – to nie jest datapack (brak pack.mcmeta), plugin ani mod")
        return inspect(path) | {"fixed": True}
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        if not any(n.startswith("data/") for n in names):
            raise WorldgenError("To paczka zasobów (wygląd), a nie datapack – brak folderu data/")
        try:
            meta = json.loads(zf.read("pack.mcmeta").decode("utf-8-sig"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise WorldgenError("Uszkodzony pack.mcmeta") from exc
    pack = meta.get("pack", {}) if isinstance(meta, dict) else {}
    desc = pack.get("description", "")
    if not isinstance(desc, str):
        desc = json.dumps(desc, ensure_ascii=False)
    return {"kind": "datapack", "name": None, "loaders": [], "pack_format": pack.get("pack_format"),
            "description": desc[:200]}


def compatibility_error(info: dict, engine: str) -> str | None:
    kind = info["kind"]
    if kind not in ENGINE_KINDS.get(engine, set()):
        allowed = " i ".join(sorted(KIND_LABEL[k] for k in ENGINE_KINDS.get(engine, set())))
        return f"{info.get('filename', 'Plik')}: to {KIND_LABEL[kind]}, a ten silnik obsługuje tylko: {allowed}"
    if kind == "mod" and not set(info.get("loaders") or []) & ENGINE_LOADERS.get(engine, set()):
        return f"{info.get('filename', 'Plik')}: mod dla {'/'.join(info['loaders'])} nie zadziała na tym silniku"
    return None


# ---------------------------------------------------------------- staging
def _cleanup() -> None:
    if not STAGE_DIR.is_dir():
        return
    cutoff = time.time() - STAGE_TTL
    for p in STAGE_DIR.iterdir():
        try:
            if p.stat().st_mtime < cutoff:
                p.unlink()
        except OSError:
            pass


def safe_filename(name: str, kind: str) -> str:
    base = SAFE_NAME.sub("_", Path(name or "").name).strip("._") or "worldgen"
    ext = ".zip" if kind == "datapack" else ".jar"
    if not base.lower().endswith(ext):
        base = base.rsplit(".", 1)[0] + ext if "." in base else base + ext
    return base[:120]


def stage(src: Path, filename: str) -> dict:
    """Inspect an uploaded file and keep it until the server is created. Returns its description + token."""
    STAGE_DIR.mkdir(parents=True, exist_ok=True)
    _cleanup()
    if src.stat().st_size > MAX_FILE:
        raise WorldgenError("Plik jest za duży (maks. 512 MB)")
    token = secrets.token_hex(12)
    data = STAGE_DIR / f"{token}.bin"
    shutil.move(str(src), data)
    try:
        info = inspect(data)
    except Exception:
        data.unlink(missing_ok=True)
        raise
    info.update({"token": token, "filename": safe_filename(filename, info["kind"]), "size": data.stat().st_size})
    (STAGE_DIR / f"{token}.json").write_text(json.dumps(info))
    return info


def staged(token: str) -> tuple[dict, Path]:
    if not TOKEN.match(token or ""):
        raise WorldgenError("Nieprawidłowy plik generatora")
    meta, data = STAGE_DIR / f"{token}.json", STAGE_DIR / f"{token}.bin"
    if not meta.is_file() or not data.is_file():
        raise WorldgenError("Wgrany plik generatora wygasł – dodaj go ponownie")
    return json.loads(meta.read_text()), data


# ---------------------------------------------------------------- applying
def level_type_value(level_type: str, mc_version: str) -> str | None:
    """server.properties value for a preset (old names before 1.19) or a custom namespaced preset."""
    level_type = (level_type or "").strip()
    if not level_type:
        return None
    if level_type in LEVEL_TYPES:
        from .providers import vkey
        new, old = LEVEL_TYPES[level_type]
        try:
            modern = vkey(mc_version) >= vkey("1.19")
        except Exception:  # noqa: BLE001 – unknown/odd version strings: assume modern
            modern = True
        return new if modern else old
    if not CUSTOM_LEVEL_TYPE.match(level_type) or len(level_type) > 100:
        raise WorldgenError("Typ świata: dozwolone są małe litery, cyfry i znaki _ . - / : (np. terralith:default)")
    return level_type


def validate(spec: dict, engine: str, mc_version: str) -> list[tuple[dict, Path]]:
    """Check everything before the server is created, so a bad file never leaves a half-made server."""
    level_type_value(spec.get("level_type", ""), mc_version)
    seed = spec.get("seed", "") or ""
    if len(seed) > 100 or any(c in seed for c in "\r\n"):
        raise WorldgenError("Ziarno może mieć najwyżej 100 znaków w jednej linii")
    generator = (spec.get("generator") or "").strip()
    if generator and not re.match(r"^[A-Za-z0-9_.-]+(:[A-Za-z0-9_./-]*)?$", generator):
        raise WorldgenError("Generator (bukkit.yml): np. Terra:OVERWORLD – litery, cyfry i znaki _ . - : /")
    tokens = spec.get("files") or []
    if len(tokens) > MAX_FILES:
        raise WorldgenError(f"Najwyżej {MAX_FILES} plików generatora")
    files = [staged(t) for t in tokens]
    for info, _ in files:
        err = compatibility_error(info, engine)
        if err:
            raise WorldgenError(err)
    if generator and not any(info["kind"] == "plugin" for info, _ in files):
        raise WorldgenError("Generator w bukkit.yml ustawia się tylko razem z pluginem generatora")
    return files


def apply(inst: "ServerInstance", spec: dict, files: list[tuple[dict, Path]], log) -> None:
    """Put the generator in place in a freshly created server, before its first start."""
    inst.dir.mkdir(parents=True, exist_ok=True)
    props_path = inst.dir / "server.properties"
    changes: dict[str, str] = {}
    level_type = level_type_value(spec.get("level_type", ""), inst.cfg.mc_version)
    if level_type:
        changes["level-type"] = level_type
    if spec.get("seed"):
        changes["level-seed"] = spec["seed"]
    if changes:
        update_properties(props_path, changes)
    level = read_properties(props_path).get("level-name") or "world"
    targets = {"datapack": inst.dir / level / "datapacks", "plugin": inst.dir / "plugins", "mod": inst.dir / "mods"}
    for info, data in files:
        dest_dir = targets[info["kind"]]
        dest_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(data), dest_dir / info["filename"])
        (STAGE_DIR / f"{info['token']}.json").unlink(missing_ok=True)
        log(f"Generator świata: {info['kind']} {info['filename']} → {dest_dir.relative_to(inst.dir)}/")
    generator = (spec.get("generator") or "").strip()
    if generator:
        bukkit = inst.dir / "bukkit.yml"
        if bukkit.exists():
            log("bukkit.yml już istnieje – ustaw generator ręcznie w sekcji worlds.")
        else:
            # Bukkit merges its defaults into this file on the first start and keeps our worlds section.
            bukkit.write_text(f"worlds:\n  {level}:\n    generator: {generator}\n", encoding="utf-8")
            log(f"Generator świata: bukkit.yml → worlds.{level}.generator = {generator}")
    if level_type:
        log(f"Typ świata: level-type={level_type}")
    if spec.get("seed"):
        log("Ziarno świata ustawione (level-seed)")


# ---------------------------------------------------------------- Modrinth
MODRINTH = "https://api.modrinth.com/v2"
# Modrinth loader tags per engine, best first – the first one a version supports decides what is downloaded.
MODRINTH_LOADERS: dict[str, list[str]] = {
    "vanilla": ["datapack"],
    "paper": ["paper", "purpur", "spigot", "bukkit", "datapack"],
    "purpur": ["purpur", "paper", "spigot", "bukkit", "datapack"],
    "fabric": ["fabric", "datapack"],
    "quilt": ["quilt", "fabric", "datapack"],
    "forge": ["forge", "datapack"],
    "neoforge": ["neoforge", "datapack"],
    "custom": ["datapack", "fabric", "quilt", "forge", "neoforge", "paper", "purpur", "spigot", "bukkit"],
}


async def modrinth_search(engine: str, mc: str | None, query: str = "", offset: int = 0, limit: int = 20) -> dict:
    """World generation projects (category "worldgen") that have a version for this engine and Minecraft."""
    from . import net
    loaders = MODRINTH_LOADERS.get(engine, ["datapack"])
    facets = [["categories:worldgen"], ["categories!=library"],
              ["project_type:mod", "project_type:datapack", "project_type:plugin"],
              [f"categories:{l}" for l in loaders]]
    if mc:
        facets.append([f"versions:{mc}"])
    data = await net.get_json(f"{MODRINTH}/search", params={
        "query": query, "limit": limit, "offset": max(0, offset), "facets": json.dumps(facets),
        "index": "relevance" if query else "downloads"})
    return {"total": data.get("total_hits", 0), "items": [{
        "id": h["project_id"], "title": h["title"], "description": h.get("description", ""),
        "icon": h.get("icon_url"), "downloads": h.get("downloads", 0), "author": h.get("author"),
        "project_type": h.get("project_type"),
        "url": f"https://modrinth.com/{h.get('project_type') or 'project'}/{h.get('slug') or h['project_id']}",
    } for h in data.get("hits", [])]}


def pick_version(versions: list[dict], loaders: list[str]) -> tuple[dict, dict] | None:
    """(version, file) for the most preferred loader; releases win over betas within a loader."""
    for loader in loaders:
        matching = [v for v in versions if loader in (v.get("loaders") or [])]
        matching.sort(key=lambda v: v.get("version_type") != "release")  # stable sort keeps newest first
        for v in matching:
            files = v.get("files") or []
            file = next((f for f in files if f.get("primary")), files[0] if files else None)
            if file and file.get("url"):
                return v, file
    return None


async def _download_and_stage(project_id: str, file: dict) -> dict:
    from . import net
    if (file.get("size") or 0) > MAX_FILE:
        raise WorldgenError("Plik jest za duży (maks. 512 MB)")
    STAGE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STAGE_DIR / f"download-{secrets.token_hex(8)}.part"
    hashes = file.get("hashes") or {}
    try:
        await net.download(file["url"], tmp, sha512=hashes.get("sha512"),
                           sha1=None if hashes.get("sha512") else hashes.get("sha1"))
        return stage(tmp, file.get("filename") or f"{project_id}.zip")
    finally:
        tmp.unlink(missing_ok=True)


async def stage_from_modrinth(project_id: str, engine: str, mc: str) -> dict:
    """Download the best matching version of a Modrinth project – plus the projects it requires –
    and stage them like uploads. Returns {"items": [...], "warnings": [...]}."""
    from . import net
    if not re.match(r"^[A-Za-z0-9]{6,12}$", project_id or ""):
        raise WorldgenError("Nieprawidłowy projekt Modrinth")
    loaders = MODRINTH_LOADERS.get(engine, ["datapack"])
    items: list[dict] = []
    warnings: list[str] = []
    seen: set[str] = set()

    async def add(pid: str, depth: int, required_by: str | None) -> None:
        if pid in seen:
            return
        seen.add(pid)
        project = await net.get_json(f"{MODRINTH}/project/{pid}")
        title = project.get("title") or pid
        if required_by and project.get("server_side") == "unsupported":
            return  # a client-only dependency is not needed on the server
        versions = await net.get_json(f"{MODRINTH}/project/{pid}/version", params={
            "loaders": json.dumps(loaders), "game_versions": json.dumps([mc])})
        picked = pick_version(versions or [], loaders)
        if not picked:
            if required_by:
                warnings.append(f"{title} (wymagany przez {required_by}) nie ma wersji dla Minecrafta {mc}")
                return
            raise WorldgenError(f"{title} nie ma wersji dla tego silnika i Minecrafta {mc}")
        version, file = picked
        info = await _download_and_stage(pid, file)
        info.update({"source": "modrinth", "project_id": pid, "title": title, "name": title,
                     "version_number": version.get("version_number"), "required_by": required_by})
        items.append(info)
        if depth < 2:
            for dep in version.get("dependencies") or []:
                if dep.get("dependency_type") == "required" and dep.get("project_id"):
                    await add(dep["project_id"], depth + 1, title)

    await add(project_id, 0, None)
    return {"items": items, "warnings": warnings}
