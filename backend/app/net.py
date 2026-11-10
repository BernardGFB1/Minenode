"""Shared HTTP client with retries, hash verification and progress reporting."""
from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

import httpx

from .config import USER_AGENT

ProgressCb = Callable[[int, Optional[int]], Awaitable[None] | None]

_client: httpx.AsyncClient | None = None


def client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            follow_redirects=True,
            timeout=httpx.Timeout(30.0, read=120.0),
            headers={"User-Agent": USER_AGENT},
            limits=httpx.Limits(max_connections=32, max_keepalive_connections=16),
        )
    return _client


async def close() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


class DownloadError(RuntimeError):
    pass


async def _retry(fn: Callable[[], Awaitable[Any]], attempts: int = 3) -> Any:
    delay = 1.0
    for i in range(attempts):
        try:
            return await fn()
        except (httpx.TransportError, httpx.HTTPStatusError, DownloadError) as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status is not None and 400 <= status < 500 and status != 429:
                raise
            if i == attempts - 1:
                raise
            await asyncio.sleep(delay)
            delay *= 2


async def get_json(url: str, *, headers: dict | None = None, params: dict | None = None) -> Any:
    async def go():
        r = await client().get(url, headers=headers, params=params)
        r.raise_for_status()
        return r.json()

    return await _retry(go)


async def post_json(url: str, body: Any, *, headers: dict | None = None) -> Any:
    async def go():
        r = await client().post(url, json=body, headers=headers)
        r.raise_for_status()
        return r.json()

    return await _retry(go)


async def get_text(url: str, *, headers: dict | None = None) -> str:
    async def go():
        r = await client().get(url, headers=headers)
        r.raise_for_status()
        return r.text

    return await _retry(go)


async def download(
    url: str,
    dest: Path,
    *,
    sha1: str | None = None,
    sha256: str | None = None,
    sha512: str | None = None,
    headers: dict | None = None,
    progress: ProgressCb | None = None,
) -> Path:
    """Stream a file to disk atomically, verifying the checksum when provided."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")

    async def go():
        hashers = {k: hashlib.new(k) for k, v in (("sha1", sha1), ("sha256", sha256), ("sha512", sha512)) if v}
        async with client().stream("GET", url, headers=headers) as r:
            r.raise_for_status()
            total = int(r.headers.get("content-length") or 0) or None
            done = 0
            with open(part, "wb") as fh:
                async for chunk in r.aiter_bytes(256 * 1024):
                    fh.write(chunk)
                    for h in hashers.values():
                        h.update(chunk)
                    done += len(chunk)
                    if progress:
                        res = progress(done, total)
                        if asyncio.iscoroutine(res):
                            await res
        expected = {"sha1": sha1, "sha256": sha256, "sha512": sha512}
        for name, h in hashers.items():
            if h.hexdigest().lower() != str(expected[name]).lower():
                part.unlink(missing_ok=True)
                raise DownloadError(f"Błędna suma kontrolna ({name}) dla {url}")
        os.replace(part, dest)
        return dest

    try:
        return await _retry(go)
    finally:
        part.unlink(missing_ok=True)
