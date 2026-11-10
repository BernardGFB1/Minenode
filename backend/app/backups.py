"""Consistent world/server backups (zip) stored in one or more destinations (panel disk, NAS folder, S3, WebDAV),
with retention, scheduled runs and restore."""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import time
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING

from . import storage as st
from .config import BACKUPS_DIR, TMP_DIR
from .models import BackupDestination

if TYPE_CHECKING:
    from .manager import Manager, ServerInstance

SKIP_SUFFIXES = (".part", ".lock")
STORED_EXT = (".jar", ".zip", ".gz", ".png", ".jpg", ".mrpack", ".xz", ".7z")


def zip_dir(src: Path, dest: Path, progress=None) -> int:
    files = [p for p in src.rglob("*") if p.is_file() and not p.name.endswith(SKIP_SUFFIXES)]
    total = sum(p.stat().st_size for p in files) or 1
    done = 0
    part = dest.with_name(dest.name + ".part")
    with zipfile.ZipFile(part, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=5, allowZip64=True) as zf:
        for p in files:
            rel = p.relative_to(src).as_posix()
            try:
                comp = zipfile.ZIP_STORED if p.suffix.lower() in STORED_EXT else zipfile.ZIP_DEFLATED
                zf.write(p, rel, compress_type=comp)
            except (FileNotFoundError, PermissionError):
                continue  # file vanished/locked while archiving
            done += p.stat().st_size if p.exists() else 0
            if progress:
                progress(done / total)
    os.replace(part, dest)
    return dest.stat().st_size


def safe_extract(zf: zipfile.ZipFile, dest: Path, strip_prefix: str = "") -> None:
    root = dest.resolve()
    for info in zf.infolist():
        name = info.filename
        if strip_prefix:
            if not name.startswith(strip_prefix):
                continue
            name = name[len(strip_prefix):]
        if not name or name.endswith("/"):
            if name:
                (root / name).mkdir(parents=True, exist_ok=True)
            continue
        target = (root / name).resolve()
        if not target.is_relative_to(root):
            raise ValueError(f"Niebezpieczna ścieżka w archiwum: {info.filename}")
        target.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(info) as src, open(target, "wb") as out:
            shutil.copyfileobj(src, out, 1024 * 1024)


LOCAL = BackupDestination(id="local", name="Dysk panelu", type="local")


class BackupManager:
    def __init__(self, manager: "Manager"):
        self.m = manager
        self._busy: set[str] = set()
        self._meta: dict[tuple, dict] = {}          # (dest, key) -> sidecar json (immutable once written)
        self._list_cache: dict[tuple, tuple[float, list]] = {}

    # ------------------------------------------------------------ destinations
    def destinations(self) -> list[BackupDestination]:
        return [LOCAL, *self.m.settings.backup_destinations]

    def destination(self, did: str) -> BackupDestination:
        for d in self.destinations():
            if d.id == did:
                return d
        raise KeyError(did)

    def storage(self, did: str) -> st.Storage:
        return st.build(self.destination(did))

    def targets_for(self, inst: "ServerInstance", wanted: list[str] | None) -> list[str]:
        known = {d.id for d in self.destinations()}
        ids = [d for d in (wanted or inst.cfg.backup.destinations or ["local"]) if d in known]
        return ids or ["local"]

    def busy(self, sid: str) -> bool:
        return sid in self._busy

    def invalidate(self, sid: str) -> None:
        for k in [k for k in self._list_cache if k[1] == sid]:
            self._list_cache.pop(k, None)

    # ------------------------------------------------------------ listing
    async def _list_dest(self, dest: BackupDestination, sid: str) -> list[dict]:
        key = (dest.id, sid)
        hit = self._list_cache.get(key)
        if hit and time.time() - hit[0] < (5 if dest.type in ("local", "folder") else 60):
            return hit[1]
        store = st.build(dest)
        objects = await store.list(sid)
        zips = [o for o in objects if o["key"].endswith(".zip")]
        sem = asyncio.Semaphore(8)

        async def meta(o: dict) -> dict:
            jkey = o["key"][:-4] + ".json"
            cached = self._meta.get((dest.id, jkey))
            if cached is None:
                async with sem:
                    try:
                        cached = json.loads(await store.read_bytes(jkey))
                    except Exception:  # noqa: BLE001 – missing/broken sidecar: fall back to file info
                        cached = {}
                self._meta[(dest.id, jkey)] = cached
            bid = o["key"].rsplit("/", 1)[-1][:-4]
            return {"id": bid, "size": o["size"], "created_at": cached.get("created_at", o["mtime"]),
                    "note": cached.get("note", ""), "auto": cached.get("auto", False),
                    "mc_version": cached.get("mc_version"), "type": cached.get("type")}

        items = await asyncio.gather(*(meta(o) for o in zips))
        self._list_cache[key] = (time.time(), items)
        return items

    async def list(self, sid: str) -> dict:
        """Backups of a server merged across destinations: [{id, …, locations: [{id, name, type, size}]}]."""
        merged: dict[str, dict] = {}
        errors: dict[str, str] = {}

        async def one(dest: BackupDestination):
            try:
                return dest, await self._list_dest(dest, sid)
            except Exception as exc:  # noqa: BLE001
                errors[dest.id] = str(exc)
                return dest, []

        for dest, items in await asyncio.gather(*(one(d) for d in self.destinations())):
            for it in items:
                e = merged.setdefault(it["id"], {**it, "locations": []})
                e["locations"].append({"id": dest.id, "name": dest.name, "type": dest.type, "size": it["size"]})
        out = sorted(merged.values(), key=lambda b: b["created_at"], reverse=True)
        return {"backups": out, "errors": errors}

    # ------------------------------------------------------------ create
    async def create(self, inst: "ServerInstance", note: str = "", auto: bool = False,
                     targets: list[str] | None = None) -> dict:
        sid = inst.cfg.id
        if sid in self._busy:
            raise RuntimeError("Kopia zapasowa jest już w trakcie tworzenia")
        self._busy.add(sid)
        ids = self.targets_for(inst, targets)
        was_online = inst.state == "online"
        bid = time.strftime("%Y%m%d-%H%M%S")
        key = f"{sid}/{bid}.zip"
        local_zip = (BACKUPS_DIR / key) if "local" in ids else (TMP_DIR / f"backup-{sid}-{bid}.zip")
        uploaded, failed = [], {}
        try:
            inst.set_job("Kopia zapasowa", "Zapisywanie świata", 0.0)
            if was_online:
                try:
                    await inst.send_command("save-off", echo=False)
                    await inst.send_command("save-all flush", echo=False)
                    await inst.wait_for_log(r"Saved the (game|world)|All dimensions are saved", 30)
                except Exception:
                    pass
            local_zip.parent.mkdir(parents=True, exist_ok=True)
            inst.panel_log(f"Tworzenie kopii zapasowej {bid}…")
            loop = asyncio.get_running_loop()
            remote = [d for d in ids if d != "local"]
            share = 0.6 if remote else 1.0

            def prog(frac: float) -> None:
                loop.call_soon_threadsafe(inst.set_job, "Kopia zapasowa", "Archiwizacja plików", frac * share)

            started = time.time()
            size = await asyncio.to_thread(zip_dir, inst.dir, local_zip, prog)
            # world saving can resume as soon as the archive exists – uploads may take a while
            if was_online and inst.running:
                with contextlib.suppress(Exception):
                    await inst.send_command("save-on", echo=False)
                was_online = False
            meta = json.dumps({"created_at": time.time(), "note": note, "auto": auto, "mc_version": inst.cfg.mc_version,
                               "type": inst.cfg.type, "server": inst.cfg.name, "server_id": sid, "size": size}).encode()
            if "local" in ids:
                await st.build(LOCAL).write_bytes(f"{sid}/{bid}.json", meta)
                uploaded.append("local")
            for n, did in enumerate(remote):
                dest = self.destination(did)
                base = share + (1 - share) * n / len(remote)

                def up(done: int, total: int, base=base, name=dest.name) -> None:
                    frac = base + (1 - share) / len(remote) * (done / max(total, 1))
                    loop.call_soon_threadsafe(inst.set_job, "Kopia zapasowa", f"Wysyłanie do {name}", frac)

                inst.set_job("Kopia zapasowa", f"Wysyłanie do {dest.name}", base)
                try:
                    store = st.build(dest)
                    await store.upload(local_zip, key, up)
                    await store.write_bytes(f"{sid}/{bid}.json", meta)
                    uploaded.append(did)
                    inst.panel_log(f"Kopia {bid} wysłana do „{dest.name}”.")
                except Exception as exc:  # noqa: BLE001 – one failing destination must not lose the others
                    failed[did] = str(exc)
                    inst.panel_log(f"BŁĄD wysyłania kopii do „{dest.name}”: {exc}")
            if not uploaded:
                raise RuntimeError("Nie udało się zapisać kopii w żadnym miejscu")
            inst.cfg.last_backup_at = time.time()
            self.m.save_server(inst.cfg)
            inst.panel_log(f"Kopia {bid} gotowa ({size / 1048576:.1f} MB, {time.time() - started:.0f} s).")
            if auto:
                for did in uploaded:
                    with contextlib.suppress(Exception):
                        await self.prune(sid, inst.cfg.backup.keep, did)
            return {"id": bid, "size": size, "uploaded": uploaded, "failed": failed}
        except Exception as exc:
            inst.panel_log(f"BŁĄD kopii zapasowej: {exc}")
            raise
        finally:
            if "local" not in ids:
                local_zip.unlink(missing_ok=True)
            local_zip.with_name(local_zip.name + ".part").unlink(missing_ok=True)
            if was_online and inst.running:
                with contextlib.suppress(Exception):
                    await inst.send_command("save-on", echo=False)
            inst.set_job(None)
            self.invalidate(sid)
            self._busy.discard(sid)

    # ------------------------------------------------------------ retention / delete
    async def prune(self, sid: str, keep: int, did: str) -> None:
        dest = self.destination(did)
        self._list_cache.pop((did, sid), None)
        autos = sorted((b for b in await self._list_dest(dest, sid) if b["auto"]), key=lambda b: b["created_at"], reverse=True)
        for b in autos[keep:]:
            await self.delete(sid, b["id"], did)

    async def delete(self, sid: str, bid: str, did: str | None = None) -> None:
        _check_id(bid)
        ids = [did] if did else [d.id for d in self.destinations()]
        for d in ids:
            store = self.storage(d)
            for ext in (".zip", ".json"):
                with contextlib.suppress(FileNotFoundError):
                    await store.delete(f"{sid}/{bid}{ext}")
            self._list_cache.pop((d, sid), None)

    def delete_all(self, sid: str) -> None:
        """Only the panel disk is cleaned when a server is deleted – external copies are kept on purpose."""
        shutil.rmtree(BACKUPS_DIR / sid, ignore_errors=True)
        self.invalidate(sid)

    # ------------------------------------------------------------ download / restore
    async def locate(self, sid: str, bid: str, did: str | None = None) -> BackupDestination:
        _check_id(bid)
        data = (await self.list(sid))["backups"]
        entry = next((b for b in data if b["id"] == bid), None)
        if not entry:
            raise FileNotFoundError(bid)
        ids = [l["id"] for l in entry["locations"]]
        if did:
            if did not in ids:
                raise FileNotFoundError(bid)
            return self.destination(did)
        # prefer something on local disk (fast), then folders, then remote services
        for kind in ("local", "folder", "s3", "webdav"):
            for l in entry["locations"]:
                if l["type"] == kind:
                    return self.destination(l["id"])
        raise FileNotFoundError(bid)

    async def restore(self, inst: "ServerInstance", bid: str, did: str | None = None) -> None:
        sid = inst.cfg.id
        dest = await self.locate(sid, bid, did)
        if sid in self._busy:
            raise RuntimeError("Trwa inna operacja na kopiach")
        self._busy.add(sid)
        tmp_zip: Path | None = None
        try:
            if inst.running:
                await inst.stop()
            store = st.build(dest)
            archive = store.local_path(f"{sid}/{bid}.zip")
            if archive is None:
                inst.set_job("Przywracanie kopii", f"Pobieranie z {dest.name}", None)
                tmp_zip = TMP_DIR / f"restore-{sid}-{bid}.zip"
                await store.download(f"{sid}/{bid}.zip", tmp_zip)
                archive = tmp_zip
            inst.set_job("Przywracanie kopii", "Rozpakowywanie", None)
            inst.panel_log(f"Przywracanie kopii {bid}…")
            staging = TMP_DIR / f"restore-{sid}-{int(time.time())}"
            old = TMP_DIR / f"old-{sid}-{int(time.time())}"

            def work() -> None:
                staging.mkdir(parents=True)
                with zipfile.ZipFile(archive) as zf:
                    safe_extract(zf, staging)
                if inst.dir.exists():
                    shutil.move(str(inst.dir), str(old))
                shutil.move(str(staging), str(inst.dir))
                shutil.rmtree(old, ignore_errors=True)

            try:
                await asyncio.to_thread(work)
            except Exception:
                if old.exists() and not inst.dir.exists():
                    shutil.move(str(old), str(inst.dir))
                shutil.rmtree(staging, ignore_errors=True)
                raise
            inst.panel_log(f"Przywrócono kopię {bid}.")
        finally:
            if tmp_zip:
                tmp_zip.unlink(missing_ok=True)
            inst.set_job(None)
            self._busy.discard(sid)


def _check_id(bid: str) -> None:
    if not bid or "/" in bid or "\\" in bid or bid.startswith(".") or len(bid) > 64:
        raise ValueError("bad backup id")
