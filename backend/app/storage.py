"""Backup storage backends: the panel disk, any mounted folder (NAS), S3-compatible object storage and WebDAV.

Every backend stores objects under keys like ``<server-id>/<backup-id>.zip`` (+ a ``.json`` sidecar with metadata),
so a destination can be browsed by hand and re-imported if the panel is reinstalled.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import time
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import AsyncIterator, Callable, Iterator
from urllib.parse import quote, unquote, urlparse

import httpx

from .config import BACKUPS_DIR, DATA_DIR, SERVERS_DIR, USER_AGENT
from .models import BackupDestination

Progress = Callable[[int, int], None]  # (bytes done, total)
SECRET_FIELDS = ("secret_key", "password")
CHUNK = 1024 * 1024


class StorageError(RuntimeError):
    pass


class Storage:
    kind = "base"

    def __init__(self, dest: BackupDestination):
        self.d = dest

    async def upload(self, src: Path, key: str, progress: Progress | None = None) -> None: ...
    async def download(self, key: str, dest: Path) -> None: ...
    async def delete(self, key: str) -> None: ...
    async def list(self, prefix: str) -> list[dict]: ...  # [{key, size, mtime}]
    async def read_bytes(self, key: str) -> bytes: ...
    async def write_bytes(self, key: str, data: bytes) -> None: ...
    def local_path(self, key: str) -> Path | None:
        return None
    def stream(self, key: str):  # sync or async iterator of bytes (for StreamingResponse)
        raise NotImplementedError

    async def test(self) -> None:
        key = ".minenode-test"
        await self.write_bytes(key, b"MineNode write test\n")
        if (await self.read_bytes(key)).strip() != b"MineNode write test":
            raise StorageError("Odczytany plik testowy różni się od zapisanego")
        await self.delete(key)


# ====================================================================== local folder (panel disk, NAS mount)

def validate_folder(path: str) -> Path:
    if not path or not path.startswith("/"):
        raise StorageError("Podaj bezwzględną ścieżkę w kontenerze, np. /backups/nas")
    p = Path(path).resolve()
    if p == DATA_DIR or p.is_relative_to(SERVERS_DIR) or DATA_DIR.is_relative_to(p):
        raise StorageError("Ten folder nie może być miejscem kopii (to katalog danych panelu)")
    if not p.exists():
        raise StorageError(f"Folder {p} nie istnieje – zamontuj go w docker-compose (volumes)")
    if not p.is_dir():
        raise StorageError(f"{p} nie jest folderem")
    if not os.access(p, os.W_OK):
        raise StorageError(f"Brak uprawnień do zapisu w {p} (sprawdź PUID/PGID)")
    return p


class FolderStorage(Storage):
    kind = "folder"

    def __init__(self, dest: BackupDestination, root: Path | None = None):
        super().__init__(dest)
        self.root = root or Path(dest.path)

    def _p(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if not p.is_relative_to(self.root.resolve()):
            raise StorageError("bad key")
        return p

    def local_path(self, key: str) -> Path | None:
        p = self._p(key)
        return p if p.exists() else None

    async def upload(self, src: Path, key: str, progress: Progress | None = None) -> None:
        dest = self._p(key)
        if src.resolve() == dest:
            return

        def copy() -> None:
            dest.parent.mkdir(parents=True, exist_ok=True)
            part = dest.with_name(dest.name + ".part")
            total, done = src.stat().st_size, 0
            with open(src, "rb") as fi, open(part, "wb") as fo:
                while chunk := fi.read(8 * CHUNK):
                    fo.write(chunk)
                    done += len(chunk)
                    if progress:
                        progress(done, total)
                fo.flush()
                os.fsync(fo.fileno())
            os.replace(part, dest)

        await asyncio.to_thread(copy)

    async def download(self, key: str, dest: Path) -> None:
        await asyncio.to_thread(shutil.copyfile, self._p(key), dest)

    async def delete(self, key: str) -> None:
        self._p(key).unlink(missing_ok=True)

    async def list(self, prefix: str) -> list[dict]:
        def scan() -> list[dict]:
            base = self._p(prefix)
            if not base.exists():
                return []
            out = []
            for f in base.iterdir():
                if f.is_file():
                    st = f.stat()
                    out.append({"key": f"{prefix.rstrip('/')}/{f.name}", "size": st.st_size, "mtime": st.st_mtime})
            return out

        return await asyncio.to_thread(scan)

    async def read_bytes(self, key: str) -> bytes:
        return await asyncio.to_thread(self._p(key).read_bytes)

    async def write_bytes(self, key: str, data: bytes) -> None:
        p = self._p(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(p.write_bytes, data)

    async def test(self) -> None:
        if self.kind == "folder":
            validate_folder(str(self.root))
        await super().test()


class LocalStorage(FolderStorage):
    kind = "local"

    def __init__(self, dest: BackupDestination):
        super().__init__(dest, BACKUPS_DIR)


# ====================================================================== S3 (AWS, MinIO, Backblaze B2, Cloudflare R2, Wasabi…)

class S3Storage(Storage):
    kind = "s3"

    def __init__(self, dest: BackupDestination):
        super().__init__(dest)
        self._client = None

    @property
    def client(self):
        if self._client is None:
            try:
                import boto3
                from botocore.config import Config
            except ImportError as exc:  # pragma: no cover
                raise StorageError("Brak biblioteki boto3") from exc
            d = self.d
            self._client = boto3.client(
                "s3", endpoint_url=d.endpoint or None, region_name=d.region or "us-east-1",
                aws_access_key_id=d.access_key or None, aws_secret_access_key=d.secret_key or None,
                config=Config(signature_version="s3v4", user_agent_extra=USER_AGENT,
                              s3={"addressing_style": "path" if d.path_style else "auto"},
                              retries={"max_attempts": 5, "mode": "standard"},
                              connect_timeout=10, read_timeout=120,
                              request_checksum_calculation="when_required",
                              response_checksum_validation="when_required"))
        return self._client

    def _k(self, key: str) -> str:
        prefix = self.d.prefix.strip("/")
        return f"{prefix}/{key}" if prefix else key

    async def _call(self, fn, *a, **kw):
        try:
            return await asyncio.to_thread(fn, *a, **kw)
        except Exception as exc:  # botocore raises many exception types
            msg = str(exc)
            code = getattr(exc, "response", {}).get("Error", {}).get("Code") if hasattr(exc, "response") else None
            if code in ("NoSuchKey", "404"):
                raise FileNotFoundError(msg) from exc
            raise StorageError(f"S3: {msg}") from exc

    async def upload(self, src: Path, key: str, progress: Progress | None = None) -> None:
        from boto3.s3.transfer import TransferConfig
        total, done = src.stat().st_size, [0]

        def cb(n: int) -> None:
            done[0] += n
            if progress:
                progress(done[0], total)

        cfg = TransferConfig(multipart_threshold=64 * CHUNK, multipart_chunksize=64 * CHUNK, max_concurrency=4)
        await self._call(self.client.upload_file, str(src), self.d.bucket, self._k(key), Callback=cb, Config=cfg)

    async def download(self, key: str, dest: Path) -> None:
        await self._call(self.client.download_file, self.d.bucket, self._k(key), str(dest))

    async def delete(self, key: str) -> None:
        await self._call(self.client.delete_object, Bucket=self.d.bucket, Key=self._k(key))

    async def list(self, prefix: str) -> list[dict]:
        def go() -> list[dict]:
            out = []
            pfx = self._k(prefix.rstrip("/") + "/")
            strip = len(self._k(""))
            for page in self.client.get_paginator("list_objects_v2").paginate(Bucket=self.d.bucket, Prefix=pfx):
                for o in page.get("Contents", []):
                    out.append({"key": o["Key"][strip:], "size": o["Size"], "mtime": o["LastModified"].timestamp()})
            return out

        return await self._call(go)

    async def read_bytes(self, key: str) -> bytes:
        r = await self._call(self.client.get_object, Bucket=self.d.bucket, Key=self._k(key))
        return await asyncio.to_thread(r["Body"].read)

    async def write_bytes(self, key: str, data: bytes) -> None:
        await self._call(self.client.put_object, Bucket=self.d.bucket, Key=self._k(key), Body=data)

    def stream(self, key: str) -> Iterator[bytes]:
        body = self.client.get_object(Bucket=self.d.bucket, Key=self._k(key))["Body"]
        try:
            yield from body.iter_chunks(CHUNK)
        finally:
            body.close()

    async def test(self) -> None:
        if not self.d.bucket:
            raise StorageError("Podaj nazwę bucketu")
        await super().test()


# ====================================================================== WebDAV (Nextcloud, ownCloud, Synology, …)

DAV = "{DAV:}"


class WebDAVStorage(Storage):
    kind = "webdav"

    def __init__(self, dest: BackupDestination):
        super().__init__(dest)
        if not dest.url.startswith(("http://", "https://")):
            raise StorageError("Adres WebDAV musi zaczynać się od http:// lub https://")
        self.base = dest.url.rstrip("/") + "/"
        auth = httpx.BasicAuth(dest.username, dest.password) if dest.username else None
        self.http = httpx.AsyncClient(auth=auth, timeout=httpx.Timeout(30, read=300, write=300),
                                      headers={"User-Agent": USER_AGENT}, follow_redirects=True)

    def _u(self, key: str) -> str:
        return self.base + quote(key.lstrip("/"))

    async def _req(self, method: str, url: str, ok=(200, 201, 204, 207), **kw) -> httpx.Response:
        try:
            r = await self.http.request(method, url, **kw)
        except httpx.HTTPError as exc:
            raise StorageError(f"WebDAV: {exc}") from exc
        if r.status_code == 404 and method in ("GET", "DELETE", "PROPFIND"):
            raise FileNotFoundError(url)
        if r.status_code in (401, 403):
            raise StorageError("WebDAV: odmowa dostępu – sprawdź login i hasło (w Nextcloud użyj hasła aplikacji)")
        if r.status_code not in ok:
            raise StorageError(f"WebDAV: {method} zwrócił {r.status_code}")
        return r

    async def _ensure_base(self) -> None:
        if getattr(self, "_base_ok", False):
            return
        r = await self.http.request("MKCOL", self.base)
        if r.status_code in (401, 403):
            raise StorageError("WebDAV: odmowa dostępu – sprawdź login i hasło (w Nextcloud użyj hasła aplikacji)")
        if r.status_code == 409:
            raise StorageError("WebDAV: folder nadrzędny z adresu nie istnieje – utwórz go albo popraw adres")
        self._base_ok = True  # 201 created, or 405 = already exists

    async def _mkdirs(self, key: str) -> None:
        await self._ensure_base()
        parts = key.strip("/").split("/")[:-1]
        path = ""
        for p in parts:
            path += p + "/"
            r = await self.http.request("MKCOL", self._u(path))
            if r.status_code not in (201, 405, 301, 302):
                if r.status_code in (401, 403):
                    raise StorageError("WebDAV: odmowa dostępu – sprawdź login i hasło")
                if r.status_code == 409:
                    raise StorageError("WebDAV: folder nadrzędny nie istnieje – sprawdź adres")

    async def upload(self, src: Path, key: str, progress: Progress | None = None) -> None:
        await self._mkdirs(key)
        total = src.stat().st_size

        async def body() -> AsyncIterator[bytes]:
            done = 0
            with open(src, "rb") as fh:
                while chunk := await asyncio.to_thread(fh.read, 4 * CHUNK):
                    done += len(chunk)
                    if progress:
                        progress(done, total)
                    yield chunk

        await self._req("PUT", self._u(key), content=body(), headers={"Content-Length": str(total)})

    async def download(self, key: str, dest: Path) -> None:
        try:
            async with self.http.stream("GET", self._u(key)) as r:
                if r.status_code == 404:
                    raise FileNotFoundError(key)
                r.raise_for_status()
                with open(dest, "wb") as fh:
                    async for chunk in r.aiter_bytes(CHUNK):
                        fh.write(chunk)
        except httpx.HTTPError as exc:
            raise StorageError(f"WebDAV: {exc}") from exc

    async def delete(self, key: str) -> None:
        try:
            await self._req("DELETE", self._u(key))
        except FileNotFoundError:
            pass

    async def list(self, prefix: str) -> list[dict]:
        url = self._u(prefix.rstrip("/") + "/")
        try:
            r = await self._req("PROPFIND", url, headers={"Depth": "1", "Content-Type": "application/xml"},
                                content=b'<?xml version="1.0"?><d:propfind xmlns:d="DAV:"><d:prop>'
                                        b'<d:getcontentlength/><d:getlastmodified/><d:resourcetype/></d:prop></d:propfind>')
        except FileNotFoundError:
            return []
        out = []
        base_path = urlparse(url).path
        for resp in ET.fromstring(r.content).iter(f"{DAV}response"):
            href = unquote(urlparse(resp.findtext(f"{DAV}href") or "").path)
            if href.rstrip("/") == unquote(base_path).rstrip("/"):
                continue
            prop = resp.find(f".//{DAV}prop")
            if prop is None or prop.find(f"{DAV}resourcetype/{DAV}collection") is not None:
                continue
            size = int(prop.findtext(f"{DAV}getcontentlength") or 0)
            lm = prop.findtext(f"{DAV}getlastmodified")
            try:
                mtime = parsedate_to_datetime(lm).timestamp() if lm else time.time()
            except (TypeError, ValueError):
                mtime = time.time()
            out.append({"key": f"{prefix.rstrip('/')}/{href.rstrip('/').rsplit('/', 1)[-1]}", "size": size, "mtime": mtime})
        return out

    async def read_bytes(self, key: str) -> bytes:
        return (await self._req("GET", self._u(key))).content

    async def write_bytes(self, key: str, data: bytes) -> None:
        await self._mkdirs(key)
        await self._req("PUT", self._u(key), content=data)

    async def stream(self, key: str) -> AsyncIterator[bytes]:
        async with self.http.stream("GET", self._u(key)) as r:
            r.raise_for_status()
            async for chunk in r.aiter_bytes(CHUNK):
                yield chunk


def build(dest: BackupDestination) -> Storage:
    return {"local": LocalStorage, "folder": FolderStorage, "s3": S3Storage, "webdav": WebDAVStorage}[dest.type](dest)


def describe(dest: BackupDestination) -> str:
    if dest.type == "local":
        return str(BACKUPS_DIR)
    if dest.type == "folder":
        return dest.path
    if dest.type == "s3":
        host = urlparse(dest.endpoint).netloc if dest.endpoint else f"AWS {dest.region or 'us-east-1'}"
        return f"s3://{dest.bucket}/{dest.prefix.strip('/')} · {host}".rstrip("/ ·")
    return dest.url
