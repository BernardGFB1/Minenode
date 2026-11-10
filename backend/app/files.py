"""File manager helpers: safe path resolution and streaming ZIP archives."""
from __future__ import annotations

import os
import queue
import threading
import zipfile
from pathlib import Path
from typing import Iterator

from fastapi import HTTPException

TEXT_LIMIT = 4 * 1024 * 1024
STORED_EXT = {".jar", ".zip", ".gz", ".png", ".jpg", ".jpeg", ".mrpack", ".xz", ".7z", ".mca"}


def resolve(root: Path, rel: str | None) -> Path:
    root = root.resolve()
    rel = (rel or "").replace("\\", "/").lstrip("/")
    p = (root / rel).resolve()
    if not p.is_relative_to(root):
        raise HTTPException(400, "Ścieżka poza katalogiem serwera")
    return p


def rel_of(root: Path, p: Path) -> str:
    return p.resolve().relative_to(root.resolve()).as_posix()


def list_dir(root: Path, rel: str) -> dict:
    d = resolve(root, rel)
    if not d.exists():
        raise HTTPException(404, "Folder nie istnieje")
    if not d.is_dir():
        raise HTTPException(400, "To nie jest folder")
    entries = []
    with os.scandir(d) as it:
        for e in it:
            try:
                st = e.stat(follow_symlinks=False)
            except OSError:
                continue
            entries.append({
                "name": e.name, "dir": e.is_dir(follow_symlinks=False),
                "size": st.st_size if not e.is_dir(follow_symlinks=False) else None, "mtime": st.st_mtime,
            })
    entries.sort(key=lambda x: (not x["dir"], x["name"].lower()))
    return {"path": rel_of(root, d) if d != root.resolve() else "", "entries": entries}


def is_text(path: Path) -> bool:
    with open(path, "rb") as fh:
        chunk = fh.read(8192)
    if b"\x00" in chunk:
        return False
    try:
        chunk.decode("utf-8")
        return True
    except UnicodeDecodeError as exc:
        return exc.start > len(chunk) - 4  # cut in the middle of a multibyte char


class _QueueWriter:
    def __init__(self, q: queue.Queue, cancelled: threading.Event):
        self.q, self.cancelled = q, cancelled

    def write(self, b) -> int:
        if not b:
            return 0
        data = bytes(b)
        while True:
            if self.cancelled.is_set():
                raise OSError("client disconnected")
            try:
                self.q.put(data, timeout=1)
                return len(data)
            except queue.Full:
                continue

    def flush(self) -> None:
        pass


def _iter_files(paths: list[Path], root: Path):
    for p in paths:
        if p.is_file():
            yield p, rel_of(root, p)
        elif p.is_dir():
            for dirpath, _dirs, files in os.walk(p):
                for f in files:
                    fp = Path(dirpath) / f
                    if fp.name.endswith(".part"):
                        continue
                    yield fp, rel_of(root, fp)


def stream_zip(paths: list[Path], root: Path) -> Iterator[bytes]:
    """Stream a ZIP of `paths` (relative names based on `root`) without a temporary file."""
    q: queue.Queue = queue.Queue(maxsize=32)
    cancelled = threading.Event()
    sentinel = object()

    def worker() -> None:
        try:
            with zipfile.ZipFile(_QueueWriter(q, cancelled), "w", zipfile.ZIP_DEFLATED, compresslevel=5) as zf:
                for fp, rel in _iter_files(paths, root):
                    try:
                        comp = zipfile.ZIP_STORED if fp.suffix.lower() in STORED_EXT else zipfile.ZIP_DEFLATED
                        zf.write(fp, rel, compress_type=comp)
                    except (FileNotFoundError, PermissionError):
                        continue
        except Exception as exc:  # noqa: BLE001 – forwarded to the consumer
            if not cancelled.is_set():
                q.put(exc)
        finally:
            if not cancelled.is_set():
                q.put(sentinel)

    threading.Thread(target=worker, daemon=True, name="zip-stream").start()
    try:
        while True:
            item = q.get()
            if item is sentinel:
                return
            if isinstance(item, Exception):
                raise item
            yield item
    finally:
        cancelled.set()
