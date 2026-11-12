"""Portable MineNode server packages (.minenodepack)."""
from __future__ import annotations

import json
import os
import shutil
import time
import re
import unicodedata
import uuid
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING

from .config import PANEL_DIR, SERVERS_DIR, TMP_DIR, VERSION
from .models import ServerConfig

if TYPE_CHECKING:
    from .manager import Manager, ServerInstance

FORMAT = "minenodepack"
SCHEMA = 1
MAX_ENTRIES = 200_000
MAX_UNCOMPRESSED = 100 * 1024 * 1024 * 1024  # 100 GiB, protects imports from zip bombs


class PackError(RuntimeError):
    pass


def _safe_member(name: str) -> bool:
    p = Path(name)
    return bool(name) and not p.is_absolute() and ".." not in p.parts and "\\" not in name


def _slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")[:40] or "server"


def _iter_server_files(root: Path):
    if not root.exists():
        return
    for base, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = [d for d in dirs if not (Path(base) / d).is_symlink()]
        for name in files:
            path = Path(base) / name
            if path.is_symlink() or name.endswith(".part"):
                continue
            yield path, path.relative_to(root).as_posix()


def create(inst: "ServerInstance") -> Path:
    """Create a self-contained package and return its temporary path."""
    if inst.running:
        raise PackError("Zatrzymaj serwer przed eksportem, aby zachować spójność świata")
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    out = TMP_DIR / f"{inst.cfg.subdomain}-{int(time.time())}-{uuid.uuid4().hex[:6]}.minenodepack"
    manifest = {
        "format": FORMAT,
        "schema": SCHEMA,
        "created_at": time.time(),
        "minenode_version": VERSION,
        "server_config": inst.cfg.model_dump(mode="json"),
        "contents": "server/",
    }
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=5) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))
        for path, rel in _iter_server_files(inst.dir) or ():
            zf.write(path, f"server/{rel}")
        players = PANEL_DIR / "players" / f"{inst.cfg.id}.json"
        if players.is_file():
            zf.write(players, "panel/players.json")
    return out


def _read_manifest(zf: zipfile.ZipFile) -> dict:
    try:
        manifest = json.loads(zf.read("manifest.json"))
    except (KeyError, ValueError, UnicodeDecodeError) as exc:
        raise PackError("Niepoprawny plik .minenodepack: brak manifest.json") from exc
    if manifest.get("format") != FORMAT:
        raise PackError("To nie jest paczka MineNode")
    schema = manifest.get("schema")
    if not isinstance(schema, int) or schema < 1 or schema > SCHEMA:
        raise PackError(f"Nieobsługiwana wersja formatu paczki: {schema}")
    if not isinstance(manifest.get("server_config"), dict):
        raise PackError("Paczka nie zawiera konfiguracji serwera")
    return manifest


def validate(path: Path) -> dict:
    try:
        with zipfile.ZipFile(path) as zf:
            bad = zf.testzip()
            if bad:
                raise PackError(f"Uszkodzony plik w paczce: {bad}")
            if len(zf.infolist()) > MAX_ENTRIES:
                raise PackError("Paczka zawiera zbyt wiele plików")
            total = sum(i.file_size for i in zf.infolist())
            if total > MAX_UNCOMPRESSED:
                raise PackError("Paczka jest zbyt duża")
            return _read_manifest(zf)
    except zipfile.BadZipFile as exc:
        raise PackError("Plik nie jest poprawnym archiwum .minenodepack") from exc


def import_pack(path: Path, manager: "Manager", name: str | None = None,
                subdomain: str | None = None) -> "ServerInstance":
    """Import into a new instance.  Extraction happens in a staging directory."""
    with zipfile.ZipFile(path) as zf:
        manifest = _read_manifest(zf)
        infos = zf.infolist()
        if len(infos) > MAX_ENTRIES or sum(i.file_size for i in infos) > MAX_UNCOMPRESSED:
            raise PackError("Paczka jest zbyt duża")
        for info in infos:
            if not _safe_member(info.filename):
                raise PackError(f"Niebezpieczna ścieżka w paczce: {info.filename}")

        raw = dict(manifest["server_config"])
        old_id = str(raw.get("id", ""))
        base_name = name or str(raw.get("name") or "Imported server")
        chosen_sub = subdomain or str(raw.get("subdomain") or base_name)
        # Use the same normalizer/uniqueness checks as normal server creation.
        chosen_sub = _slugify(chosen_sub)
        if not subdomain:
            original = chosen_sub
            n = 2
            while chosen_sub in {i.cfg.subdomain for i in manager.instances.values()}:
                chosen_sub = f"{original}-{n}"
                n += 1
        new_id = uuid.uuid4().hex[:10]
        raw.update({"id": new_id, "name": base_name.strip() or "Imported server",
                    "subdomain": chosen_sub, "internal_port": manager.allocate_port(),
                    "voice_port": manager.allocate_voice_port() if raw.get("voice_enabled") else None,
                    "created_at": time.time()})
        cfg = ServerConfig.model_validate(raw)
        manager.check_unique(cfg.subdomain, cfg.aliases)

        staging = TMP_DIR / f"import-{new_id}"
        try:
            staging.mkdir(parents=True, exist_ok=False)
            for info in infos:
                if not info.filename.startswith("server/") or info.is_dir():
                    continue
                rel = info.filename.removeprefix("server/")
                if not _safe_member(rel):
                    raise PackError(f"Niebezpieczna ścieżka w paczce: {rel}")
                dest = (staging / rel).resolve()
                if not dest.is_relative_to(staging.resolve()):
                    raise PackError("Niebezpieczna ścieżka w paczce")
                dest.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, open(dest, "wb") as dst:
                    shutil.copyfileobj(src, dst, 1024 * 1024)
            # The instance is registered only after the staging directory is ready.
            final = SERVERS_DIR / new_id
            os.replace(staging, final)
            from .manager import ServerInstance
            inst = ServerInstance(manager, cfg)
            manager.instances[new_id] = inst
            manager.save_server(cfg)
            if not manager.settings.default_server_id:
                settings = manager.settings
                settings.default_server_id = new_id
                manager.save_settings(settings)
            panel_players = next((i for i in infos if i.filename == "panel/players.json"), None)
            if panel_players:
                (PANEL_DIR / "players").mkdir(parents=True, exist_ok=True)
                with zf.open(panel_players) as src, open(PANEL_DIR / "players" / f"{new_id}.json", "wb") as dst:
                    shutil.copyfileobj(src, dst)
            if manager.bedrock:
                manager.bedrock.request_apply()
            return inst
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise
