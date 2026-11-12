import struct
from types import SimpleNamespace

from app.manager import DEFAULT_ICON, ServerInstance


def test_bundled_icon_is_a_64x64_png():
    data = DEFAULT_ICON.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", data[16:24])  # IHDR
    assert (width, height) == (64, 64)  # Minecraft only shows a 64×64 server-icon.png
    assert len(data) < 64 * 1024  # the proxy sends icons up to 64 KiB for offline servers


def test_default_icon_is_added_when_missing(tmp_path):
    inst = SimpleNamespace(dir=tmp_path)
    assert ServerInstance.ensure_default_icon(inst) is True
    assert (tmp_path / "server-icon.png").read_bytes() == DEFAULT_ICON.read_bytes()
    assert ServerInstance.ensure_default_icon(inst) is False  # second call is a no-op


def test_own_icon_is_kept(tmp_path):
    own = tmp_path / "server-icon.png"
    own.write_bytes(b"modpack icon")
    assert ServerInstance.ensure_default_icon(SimpleNamespace(dir=tmp_path)) is False
    assert own.read_bytes() == b"modpack icon"


def _png(width, height):
    import zlib
    raw = b"".join(b"\x00" + b"\x00\x00\x00\xff" * width for _ in range(height))
    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def test_icon_validation():
    import pytest
    from fastapi import HTTPException
    from app.api import validate_server_icon

    validate_server_icon(_png(64, 64))
    validate_server_icon(DEFAULT_ICON.read_bytes())
    for bad in (_png(128, 128), _png(64, 32), b"GIF89a" + b"\x00" * 40, b"", b"\x89PNG\r\n\x1a\n" + b"\x00" * (70 * 1024)):
        with pytest.raises(HTTPException):
            validate_server_icon(bad)


def test_set_and_remove_icon(tmp_path):
    inst = SimpleNamespace(dir=tmp_path)
    data = _png(64, 64)
    ServerInstance.set_icon(inst, data)
    assert (tmp_path / "server-icon.png").read_bytes() == data
    assert not (tmp_path / ".server-icon.png.tmp").exists()
    assert ServerInstance.icon_version(inst) > 0
    ServerInstance.remove_icon(inst)
    ServerInstance.remove_icon(inst)  # removing a missing icon is fine
    assert not (tmp_path / "server-icon.png").exists()
    assert ServerInstance.icon_version(inst) == 0
