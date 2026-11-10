"""Consistent world/server backups (zip), retention, scheduled runs and restore."""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING

from .config import BACKUPS_DIR, TMP_DIR
from .store import atomic_write_text

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


class BackupManager:
    def __init__(self, manager: "Manager"):
        self.m = manager
        self._busy: set[str] = set()

    def dir(self, sid: str) -> Path:
        return BACKUPS_DIR / sid

    def busy(self, sid: str) -> bool:
        return sid in self._busy

    def list(self, sid: str) -> list[dict]:
        d = self.dir(sid)
        if not d.exists():
            return []
        out = []
        for z in d.glob("*.zip"):
            meta = {}
            mf = z.with_suffix(".json")
            if mf.exists():
                try:
                    meta = json.loads(mf.read_text())
                except json.JSONDecodeError:
                    pass
            st = z.stat()
            out.append({"id": z.stem, "size": st.st_size, "created_at": meta.get("created_at", st.st_mtime),
                        "note": meta.get("note", ""), "auto": meta.get("auto", False),
                        "mc_version": meta.get("mc_version"), "type": meta.get("type")})
        out.sort(key=lambda b: b["created_at"], reverse=True)
        return out

    def path(self, sid: str, bid: str) -> Path:
        if "/" in bid or "\\" in bid or bid.startswith("."):
            raise ValueError("bad backup id")
        p = self.dir(sid) / f"{bid}.zip"
        if not p.exists():
            raise FileNotFoundError(bid)
        return p

    async def create(self, inst: "ServerInstance", note: str = "", auto: bool = False) -> dict:
        sid = inst.cfg.id
        if sid in self._busy:
            raise RuntimeError("Kopia zapasowa jest już w trakcie tworzenia")
        self._busy.add(sid)
        was_online = inst.state == "online"
        try:
            inst.set_job("Kopia zapasowa", "Zapisywanie świata", 0.0)
            if was_online:
                try:
                    await inst.send_command("save-off", echo=False)
                    await inst.send_command("save-all flush", echo=False)
                    await inst.wait_for_log(r"Saved the (game|world)|All dimensions are saved", 30)
                except Exception:
                    pass
            bid = time.strftime("%Y%m%d-%H%M%S")
            dest = self.dir(sid) / f"{bid}.zip"
            dest.parent.mkdir(parents=True, exist_ok=True)
            inst.panel_log(f"Tworzenie kopii zapasowej {bid}…")
            loop = asyncio.get_running_loop()

            def prog(frac: float) -> None:
                loop.call_soon_threadsafe(inst.set_job, "Kopia zapasowa", "Archiwizacja plików", frac)

            started = time.time()
            size = await asyncio.to_thread(zip_dir, inst.dir, dest, prog)
            meta = {"created_at": time.time(), "note": note, "auto": auto,
                    "mc_version": inst.cfg.mc_version, "type": inst.cfg.type}
            atomic_write_text(dest.with_suffix(".json"), json.dumps(meta))
            inst.cfg.last_backup_at = time.time()
            self.m.save_server(inst.cfg)
            inst.panel_log(f"Kopia {bid} gotowa ({size / 1048576:.1f} MB, {time.time() - started:.0f} s).")
            if auto:
                self.prune(sid, inst.cfg.backup.keep)
            return {"id": bid, "size": size}
        except Exception as exc:
            inst.panel_log(f"BŁĄD kopii zapasowej: {exc}")
            raise
        finally:
            if was_online and inst.running:
                try:
                    await inst.send_command("save-on", echo=False)
                except Exception:
                    pass
            inst.set_job(None)
            self._busy.discard(sid)

    def prune(self, sid: str, keep: int) -> None:
        autos = [b for b in self.list(sid) if b["auto"]]
        for b in autos[keep:]:
            self.delete(sid, b["id"])

    def delete(self, sid: str, bid: str) -> None:
        p = self.path(sid, bid)
        p.unlink(missing_ok=True)
        p.with_suffix(".json").unlink(missing_ok=True)

    def delete_all(self, sid: str) -> None:
        shutil.rmtree(self.dir(sid), ignore_errors=True)

    async def restore(self, inst: "ServerInstance", bid: str) -> None:
        sid = inst.cfg.id
        archive = self.path(sid, bid)
        if sid in self._busy:
            raise RuntimeError("Trwa inna operacja na kopiach")
        self._busy.add(sid)
        try:
            if inst.running:
                await inst.stop()
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
            inst.set_job(None)
            self._busy.discard(sid)
