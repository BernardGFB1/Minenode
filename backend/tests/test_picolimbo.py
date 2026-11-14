import asyncio
import hashlib
import io
import json
import tarfile

import pytest

from app import auth_lobby, net, picolimbo


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(picolimbo, "BIN_DIR", tmp_path / "bin")
    monkeypatch.setattr(picolimbo, "BINARY", tmp_path / "bin" / "pico_limbo")
    monkeypatch.setattr(picolimbo, "VERSION_FILE", tmp_path / "bin" / "version.json")
    monkeypatch.setattr(picolimbo, "TMP_DIR", tmp_path / "tmp")
    monkeypatch.setattr(picolimbo, "LOCAL_OVERRIDE", None)
    monkeypatch.setattr(picolimbo, "arch", lambda: "x86_64")


def archive(content=b"#!/bin/sh\necho limbo\n", extra=()):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in [*extra, ("pico_limbo", content)]:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def release(tag, published, data, *, digest=True, draft=False, arch="x86_64"):
    name = picolimbo.asset_name(arch)
    asset = {"name": name, "size": len(data), "browser_download_url": f"https://dl/{tag}/{name}"}
    if digest:
        asset["digest"] = "sha256:" + hashlib.sha256(data).hexdigest()
    return {"tag_name": tag, "published_at": published, "draft": draft, "assets": [asset]}


@pytest.fixture
def github(monkeypatch):
    """Fake GitHub: a list of releases and the files behind their download URLs."""
    state = {"releases": [], "files": {}, "downloads": 0}

    async def get_json(url, **kw):
        assert url == f"https://api.github.com/repos/{picolimbo.REPO}/releases"
        return state["releases"]

    async def download(url, dest, *, sha256=None, **kw):
        data = state["files"][url]
        if sha256:
            assert hashlib.sha256(data).hexdigest() == sha256, "checksum mismatch"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        state["downloads"] += 1
        return dest

    monkeypatch.setattr(net, "get_json", get_json)
    monkeypatch.setattr(net, "download", download)

    def publish(tag, published, data=None, **kw):
        data = data if data is not None else archive(f"binary {tag}".encode())
        rel = release(tag, published, data, **kw)
        state["releases"].append(rel)
        state["files"][rel["assets"][0]["browser_download_url"]] = data
        return rel

    state["publish"] = publish
    return state


def test_display_version():
    assert picolimbo.display_version("picolimbo-v1.15.0+mc26.3-ab12cd34") == "1.15.0+mc26.3"
    assert picolimbo.display_version("picolimbo-v1.16.0-beta.1+mc26.4-0011aabb") == "1.16.0-beta.1+mc26.4"
    assert picolimbo.display_version(None) is None


def test_newest_release_ignores_other_releases_drafts_and_other_cpus():
    data = archive()
    rels = [
        release("picolimbo-v1.14.1+mc26.3-aaaaaaaa", "2026-09-01T00:00:00Z", data),
        release("v2.0.0", "2026-12-01T00:00:00Z", data),  # a MineNode app release, not PicoLimbo
        release("picolimbo-v1.16.0+mc26.4-bbbbbbbb", "2026-11-30T00:00:00Z", data, draft=True),
        release("picolimbo-v1.15.0+mc26.3-cccccccc", "2026-10-11T00:00:00Z", data),
        release("picolimbo-v1.15.1+mc26.3-dddddddd", "2026-10-20T00:00:00Z", data, arch="aarch64"),
    ]
    assert picolimbo.newest_release(rels, "x86_64")["tag_name"] == "picolimbo-v1.15.0+mc26.3-cccccccc"
    assert picolimbo.newest_release(rels, "aarch64")["tag_name"] == "picolimbo-v1.15.1+mc26.3-dddddddd"
    assert picolimbo.newest_release([], "x86_64") is None


def test_first_install_then_update_only_when_newer(github):
    up = picolimbo.Updater()
    github["publish"]("picolimbo-v1.15.0+mc26.3-aaaaaaaa", "2026-10-11T00:00:00Z")
    path = asyncio.run(up.binary())
    assert path.read_bytes().startswith(b"binary picolimbo-v1.15.0")
    assert path.stat().st_mode & 0o111  # executable
    assert json.loads(picolimbo.VERSION_FILE.read_text())["version"] == "1.15.0+mc26.3"

    assert asyncio.run(up.update()) is False  # same release: nothing downloaded again
    assert github["downloads"] == 1

    github["publish"]("picolimbo-v1.16.0+mc26.4-bbbbbbbb", "2026-12-01T00:00:00Z")
    assert asyncio.run(up.update()) is True
    assert path.read_bytes().startswith(b"binary picolimbo-v1.16.0")
    status = up.status()
    assert status["version"] == "1.16.0+mc26.4" and status["latest"] == "1.16.0+mc26.4" and status["error"] is None


def test_only_the_binary_is_taken_from_the_archive(github, tmp_path):
    evil = archive(extra=[("../../escape.sh", b"rm -rf /"), ("/abs/path", b"x")])
    github["publish"]("picolimbo-v1.15.0+mc26.3-aaaaaaaa", "2026-10-11T00:00:00Z", evil)
    asyncio.run(picolimbo.Updater().binary())
    assert not (tmp_path / "escape.sh").exists() and not (tmp_path.parent / "escape.sh").exists()
    assert sorted(p.name for p in picolimbo.BIN_DIR.iterdir()) == ["pico_limbo", "version.json"]


def test_bad_or_missing_releases_keep_the_installed_binary(github):
    up = picolimbo.Updater()
    with pytest.raises(picolimbo.PicoLimboError, match="nie ma jeszcze wydania"):
        asyncio.run(up.binary())  # nothing released yet

    github["publish"]("picolimbo-v1.15.0+mc26.3-aaaaaaaa", "2026-10-11T00:00:00Z")
    asyncio.run(up.binary())
    good = picolimbo.BINARY.read_bytes()

    rel = github["publish"]("picolimbo-v1.16.0+mc26.4-bbbbbbbb", "2026-12-01T00:00:00Z")
    github["files"][rel["assets"][0]["browser_download_url"]] = b"tampered"  # checksum no longer matches
    assert asyncio.run(up.update()) is False
    assert picolimbo.BINARY.read_bytes() == good and "Aktualizacja PicoLimbo" in up.error

    github["publish"]("picolimbo-v1.17.0+mc26.4-cccccccc", "2026-12-05T00:00:00Z", digest=False)  # no checksum
    assert asyncio.run(up.update()) is False
    assert picolimbo.BINARY.read_bytes() == good and "sumy kontrolnej" in up.error


def test_local_binary_override(tmp_path, monkeypatch):
    local = tmp_path / "my_pico_limbo"
    local.write_text("x")
    monkeypatch.setattr(picolimbo, "LOCAL_OVERRIDE", str(local))
    up = picolimbo.Updater()
    assert asyncio.run(up.binary()) == local
    assert asyncio.run(up.update()) is False and up.status()["source"] == "local"


def test_lobby_restarts_only_after_an_update(monkeypatch):
    lobby = auth_lobby.AuthLobby(manager=None)
    calls = []

    async def stop():
        calls.append("stop")

    async def start():
        calls.append("start")

    monkeypatch.setattr(lobby, "_stop_process", stop)
    monkeypatch.setattr(lobby, "start", start)

    async def no_update():
        return False

    async def updated():
        return True

    monkeypatch.setattr(lobby.updater, "update", no_update)
    assert asyncio.run(lobby.check_for_update()) is False and calls == []
    monkeypatch.setattr(lobby.updater, "update", updated)
    assert asyncio.run(lobby.check_for_update()) is True and calls == ["stop", "start"]
