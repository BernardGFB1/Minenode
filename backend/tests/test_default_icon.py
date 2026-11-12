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
