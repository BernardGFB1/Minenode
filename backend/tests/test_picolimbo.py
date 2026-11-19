import asyncio
import io
import json
import tarfile
from types import SimpleNamespace

import pytest

from app import auth_lobby, net, picolimbo


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(picolimbo, "BIN_DIR", tmp_path / "bin")
    monkeypatch.setattr(picolimbo, "BINARY", tmp_path / "bin" / "pico_limbo")
    monkeypatch.setattr(picolimbo, "VERSION_FILE", tmp_path / "bin" / "version.json")
    monkeypatch.setattr(picolimbo, "BUILD_DIR", tmp_path / "build")
    monkeypatch.setattr(picolimbo, "RUST_DIR", tmp_path / "rust")
    monkeypatch.setattr(picolimbo, "TMP_DIR", tmp_path / "tmp")
    monkeypatch.setattr(picolimbo, "LOCAL_OVERRIDE", None)


@pytest.fixture
def upstream(monkeypatch):
    """The author's newest release and a fake compiler that records what it built."""
    state = {"tag": "v1.15.0+mc26.3", "builds": [], "fail": False}

    async def latest_tag():
        return state["tag"]

    async def compile_release(tag, build):
        if state["fail"]:
            build.note("error[E0425]: cannot find value")
            raise picolimbo.PicoLimboError("Polecenie cargo zakończyło się błędem (101)")
        state["builds"].append(tag)
        picolimbo.BIN_DIR.mkdir(parents=True, exist_ok=True)
        picolimbo.BINARY.write_text(f"binary {tag}")
        info = {"tag": tag, "version": picolimbo.display_version(tag), "patch": picolimbo.patch_id(),
                "repo": picolimbo.REPO, "source": "build", "built_at": 1.0}
        picolimbo.VERSION_FILE.write_text(json.dumps(info))
        return info

    monkeypatch.setattr(picolimbo, "latest_tag", latest_tag)
    monkeypatch.setattr(picolimbo, "compile_release", compile_release)
    return state


def test_source_comes_from_the_authors_repository():
    assert picolimbo.REPO == "Quozul/PicoLimbo"
    assert picolimbo.source_url("v1.15.0+mc26.3") == \
        "https://github.com/Quozul/PicoLimbo/archive/refs/tags/v1.15.0%2Bmc26.3.tar.gz"
    assert picolimbo.PATCH.is_file() and b"pico_limbo/src/auth.rs" in picolimbo.PATCH.read_bytes()
    assert picolimbo.display_version("v1.15.0+mc26.3") == "1.15.0+mc26.3"


def test_latest_tag_uses_the_release_page_when_the_api_refuses(monkeypatch):
    async def api_limited(url, **kw):
        raise RuntimeError("403 rate limit exceeded")

    async def head(url, follow_redirects=False):
        assert url == "https://github.com/Quozul/PicoLimbo/releases/latest"
        return SimpleNamespace(headers={"location": "https://github.com/Quozul/PicoLimbo/releases/tag/v1.16.0+mc26.4"})

    monkeypatch.setattr(net, "get_json", api_limited)
    monkeypatch.setattr(net, "client", lambda: SimpleNamespace(head=head))
    assert asyncio.run(picolimbo.latest_tag()) == "v1.16.0+mc26.4"


def test_build_only_when_upstream_or_login_module_changes(upstream, monkeypatch):
    up = picolimbo.Updater()
    path = asyncio.run(up.binary())
    assert path.read_text() == "binary v1.15.0+mc26.3" and upstream["builds"] == ["v1.15.0+mc26.3"]
    assert asyncio.run(up.update()) is False  # same release, same module: no rebuild
    upstream["tag"] = "v1.16.0+mc26.4"
    assert asyncio.run(up.update()) is True and upstream["builds"][-1] == "v1.16.0+mc26.4"
    monkeypatch.setattr(picolimbo, "patch_id", lambda: "deadbeef")  # login module changed
    assert asyncio.run(up.update()) is True and len(upstream["builds"]) == 3
    s = up.status()
    assert s["version"] == "1.16.0+mc26.4" and s["source"] == "build" and s["repo"] == "Quozul/PicoLimbo"
    assert not s["building"] and s["error"] is None and not s["legacy"]


def test_a_binary_from_minenode_releases_is_replaced_by_a_local_build(upstream):
    picolimbo.BIN_DIR.mkdir(parents=True)
    picolimbo.BINARY.write_text("old prebuilt")
    picolimbo.VERSION_FILE.write_text(json.dumps({"tag": "picolimbo-v1.15.0+mc26.3-7c0fed69",
                                                  "repo": "BernardGFB1/Minenode"}))
    up = picolimbo.Updater()
    assert up.ready_binary() == picolimbo.BINARY  # keeps the lobby running until the build is done
    assert up.status()["legacy"] and up.status()["version"] is None and up.needs_build()
    assert asyncio.run(up.update()) is True
    assert picolimbo.BINARY.read_text() == "binary v1.15.0+mc26.3" and not up.needs_build()


def test_a_failing_build_keeps_the_old_one_and_is_not_retried_in_a_loop(upstream):
    up = picolimbo.Updater()
    asyncio.run(up.binary())
    upstream["tag"] = "v1.16.0+mc26.4"
    upstream["fail"] = True
    assert asyncio.run(up.update()) is False
    assert picolimbo.BINARY.read_text() == "binary v1.15.0+mc26.3"
    assert "cargo" in up.error and "E0425" in up.error  # the log tail is shown in the panel
    upstream["fail"] = False
    assert asyncio.run(up.update()) is False and upstream["builds"] == ["v1.15.0+mc26.3"]  # not again yet
    assert asyncio.run(up.update(force=True)) is True  # the panel button retries at once


def test_missing_compiler_is_reported(monkeypatch):
    monkeypatch.setattr(picolimbo.shutil, "which", lambda name: None)
    with pytest.raises(picolimbo.PicoLimboError, match="gcc"):
        asyncio.run(picolimbo.compile_release("v1.15.0+mc26.3", picolimbo.Build()))


def test_source_archive_cannot_write_outside_the_build_folder(tmp_path):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        def add(name, data=b"x", kind=tarfile.REGTYPE, link=""):
            info = tarfile.TarInfo(name)
            info.type, info.linkname, info.size = kind, link, len(data) if kind == tarfile.REGTYPE else 0
            tar.addfile(info, io.BytesIO(data) if kind == tarfile.REGTYPE else None)
        add("PicoLimbo-1.15.0/Cargo.toml", b"[workspace]")
        add("PicoLimbo-1.15.0/pico_limbo/src/main.rs", b"fn main(){}")
        add("PicoLimbo-1.15.0/../../escape.sh", b"rm -rf /")
        add("/abs/path", b"x")
        add("PicoLimbo-1.15.0/link", kind=tarfile.SYMTYPE, link="/etc/passwd")
    archive = tmp_path / "src.tar.gz"
    archive.write_bytes(buf.getvalue())
    root = picolimbo._extract_source(archive, tmp_path / "out")
    assert (root / "pico_limbo" / "src" / "main.rs").is_file()
    assert not (tmp_path / "escape.sh").exists() and not (root / "link").exists()


def test_local_binary_override(tmp_path, monkeypatch):
    local = tmp_path / "my_pico_limbo"
    local.write_text("x")
    monkeypatch.setattr(picolimbo, "LOCAL_OVERRIDE", str(local))
    up = picolimbo.Updater()
    assert asyncio.run(up.binary()) == local
    assert asyncio.run(up.update()) is False and up.status()["source"] == "local"


def test_lobby_start_does_not_wait_for_the_first_build(upstream):
    async def scenario():
        lobby = auth_lobby.AuthLobby(manager=None)
        started = asyncio.Event()
        orig = picolimbo.compile_release

        async def slow(tag, build):
            started.set()
            await asyncio.sleep(3600)

        picolimbo.compile_release = slow
        try:
            await asyncio.wait_for(lobby.start(), 1)  # returns at once
            await asyncio.wait_for(started.wait(), 1)
            assert "kompilowane" in lobby.error and lobby.updater.building
        finally:
            await lobby.stop()
            picolimbo.compile_release = orig
    asyncio.run(scenario())


def test_lobby_restarts_only_after_an_update(monkeypatch):
    lobby = auth_lobby.AuthLobby(manager=None)
    calls = []

    async def stop():
        calls.append("stop")

    async def start():
        calls.append("start")

    monkeypatch.setattr(lobby, "_stop_process", stop)
    monkeypatch.setattr(lobby, "start", start)

    async def no_update(force=False):
        return False

    async def updated(force=False):
        return True

    monkeypatch.setattr(lobby.updater, "update", no_update)
    assert asyncio.run(lobby.check_for_update()) is False and calls == []
    monkeypatch.setattr(lobby.updater, "update", updated)
    assert asyncio.run(lobby.check_for_update()) is True and calls == ["stop", "start"]
