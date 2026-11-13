"""Read-only inspection of Minecraft client packets for a session fingerprint.

The proxy sees the raw TCP stream.  This module extracts the two packets that are
stable enough to describe a client without storing anything sensitive:

* Serverbound *Client Information* (also called Client Settings) – locale, view
  distance, chat settings, visible skin parts and main hand.
* Serverbound *Plugin Message* – the client brand and the channels/mod payloads
  the client announces.

Packet IDs change between Minecraft versions and the stream may be compressed,
so nothing here depends on a hard-coded packet ID table.  Instead, complete
frames are collected and both their raw and decompressed forms are checked
structurally.  The sniffer is strictly passive: malformed data only stops the
inspection, never the proxied connection.

Only channel names and payload hashes are kept.  Plugin message payloads can
contain arbitrary data and are never written to disk or exposed through the API.
"""
from __future__ import annotations

import hashlib
import re
import time
import zlib

MAX_FRAME = 2 * 1024 * 1024
MAX_DECOMPRESSED = 2 * 1024 * 1024
MAX_SNIFF_SECONDS = 5 * 60
MAX_PLUGIN_CHANNELS = 32
MAX_PLUGIN_PAYLOAD = 1024 * 1024
NOTIFY_INTERVAL = 1.5

_LOCALE_RE = re.compile(r"^[a-z]{2,3}(?:[_-][a-z0-9]{2,8})?$", re.IGNORECASE)
_CHANNEL_RE = re.compile(r"^[A-Za-z0-9_|:./+-]{1,128}$")
_LEGACY_CHANNELS = {"REGISTER", "UNREGISTER", "BungeeCord"}

_SKIN_PARTS = (
    (0x01, "cape"),
    (0x02, "jacket"),
    (0x04, "left_sleeve"),
    (0x08, "right_sleeve"),
    (0x10, "left_pants"),
    (0x20, "right_pants"),
    (0x40, "hat"),
)


def _varint(buf: bytes | bytearray, pos: int) -> tuple[int | None, int]:
    """Decode a VarInt; return (None, pos) when the buffer is incomplete/invalid."""
    value = 0
    for index in range(5):
        if pos >= len(buf):
            return None, pos
        byte = buf[pos]
        pos += 1
        value |= (byte & 0x7F) << (7 * index)
        if not byte & 0x80:
            if value & (1 << 31):
                value -= 1 << 32
            return value, pos
    return None, pos


def _string(buf: bytes | bytearray, pos: int, max_length: int) -> tuple[str | None, int]:
    length, pos = _varint(buf, pos)
    if length is None or length < 0 or length > max_length * 4 or pos + length > len(buf):
        return None, pos
    try:
        return bytes(buf[pos:pos + length]).decode("utf-8"), pos + length
    except UnicodeDecodeError:
        return None, pos


def parse_client_information(payload: bytes, protocol: int | None = None) -> dict | None:
    """Parse a Client Information packet payload (without its packet ID).

    The parser accepts every protocol generation: 1.7 clients send chat flags
    and a cape bit, 1.8 sends separate chat fields but no main hand, and 1.9+
    append a main hand plus (in newer versions) text filtering, server listings
    and a particle status.  Values are checked tightly enough to avoid matching
    random packets in the stream.
    """
    locale, pos = _string(payload, 0, 16)
    if locale is None:
        return None
    locale = locale.strip().lower()
    if not _LOCALE_RE.fullmatch(locale):
        return None
    if pos >= len(payload):
        return None
    view_distance = payload[pos]
    pos += 1
    if not 1 <= view_distance <= 64:
        return None

    if protocol is not None and 0 < protocol <= 5:  # 1.7.2–1.7.10
        if pos + 3 > len(payload):
            return None
        chat_flags, difficulty, show_cape = payload[pos], payload[pos + 1], payload[pos + 2]
        if chat_flags & 3 > 2 or difficulty > 3 or show_cape not in (0, 1):
            return None
        if pos + 3 != len(payload):
            return None
        return {
            "locale": locale,
            "view_distance": view_distance,
            # Bit 2 of the 1.7 chat flags enables colours.
            "chat_mode": chat_flags & 3,
            "chat_colors": bool(chat_flags & 4),
            "skin_parts": show_cape,
            "skin_parts_enabled": ["cape"] if show_cape else [],
            "main_hand": None,
            "text_filtering": None,
            "allow_listings": None,
            "particle_status": None,
        }

    chat_mode, pos = _varint(payload, pos)
    if chat_mode not in (0, 1, 2):
        return None
    if pos >= len(payload):
        return None
    chat_colors = payload[pos]
    pos += 1
    if chat_colors not in (0, 1):
        return None
    if pos >= len(payload):
        return None
    skin_parts = payload[pos]
    pos += 1

    if protocol == 47 or pos >= len(payload):  # 1.8 has no main-hand field
        main_hand = None
    else:
        main_hand, pos = _varint(payload, pos)
        if main_hand not in (0, 1):
            return None

    # 1.17+: text filtering, 1.18+: server listings, 1.21.2+: particle status.
    # Allow a few extra small fields so future protocol additions still parse.
    options: list[int] = []
    while pos < len(payload):
        value, next_pos = _varint(payload, pos)
        if value is None or next_pos <= pos or len(options) >= 8 or not 0 <= value <= 3:
            return None
        options.append(value)
        pos = next_pos
    if pos != len(payload):
        return None

    return {
        "locale": locale,
        "view_distance": view_distance,
        "chat_mode": chat_mode,
        "chat_colors": bool(chat_colors),
        "skin_parts": skin_parts,
        "skin_parts_enabled": [name for bit, name in _SKIN_PARTS if skin_parts & bit],
        "main_hand": main_hand,
        "text_filtering": bool(options[0]) if len(options) >= 1 else None,
        "allow_listings": bool(options[1]) if len(options) >= 2 else None,
        "particle_status": options[2] if len(options) >= 3 else None,
    }


def parse_plugin_message(payload: bytes) -> tuple[str, bytes] | None:
    """Return ``(channel, data)`` for a Plugin Message payload, or None."""
    channel, pos = _string(payload, 0, 256)
    if channel is None:
        return None
    channel = channel.strip()
    if not channel or not _CHANNEL_RE.fullmatch(channel):
        return None
    if ":" not in channel and "|" not in channel and channel not in _LEGACY_CHANNELS:
        return None

    length, pos = _varint(payload, pos)
    if length is None or length < 0 or length > MAX_PLUGIN_PAYLOAD or pos + length != len(payload):
        return None
    return channel, bytes(payload[pos:])


def _decode_brand(channel: str, data: bytes) -> str | None:
    if channel.lower() not in ("minecraft:brand", "mc|brand"):
        return None
    text, end = _string(data, 0, 256)
    if text is not None and end == len(data):
        return text.strip()
    return data[:64].decode("utf-8", "replace").strip()


def _candidate_bodies(body: bytes):
    """Yield possible uncompressed packet bodies for one frame.

    Compression is enabled by the server mid-session and there is no reliable
    way to know it from the serverbound stream alone.  A frame is therefore
    checked both as-is and with its optional data-length + zlib envelope.
    """
    yield body

    data_length, pos = _varint(body, 0)
    if data_length is None or pos >= len(body):
        return
    if data_length == 0:
        # Compression is on but this packet was below the threshold.  The rest
        # is the complete uncompressed packet, including its packet ID.
        yield body[pos:]
        return
    if not 0 < data_length <= MAX_DECOMPRESSED:
        return
    try:
        decompressor = zlib.decompressobj()
        out = decompressor.decompress(body[pos:], data_length + 1)
        if len(out) != data_length or not decompressor.eof or decompressor.unused_data:
            return
    except zlib.error:
        return
    yield out


class PacketSniffer:
    """Passively collect Client Information and Plugin Message packets."""

    def __init__(self, protocol: int = -1, on_capture=None):
        self.protocol = protocol
        self.client_info: dict | None = None
        self.plugin_messages: dict[str, dict] = {}
        self.brand: str | None = None
        self.frames = 0
        self.captured_at: int | None = None
        self.active = True
        self.buffer = bytearray()
        self._started = time.monotonic()
        self._on_capture = on_capture
        self._last_notify = 0.0
        self._pending = False

    def feed(self, data: bytes) -> None:
        """Feed raw bytes from the client → server direction."""
        if not self.active or not data:
            return
        if time.monotonic() - self._started > MAX_SNIFF_SECONDS:
            self.active = False
            return
        self.buffer.extend(data)
        try:
            self._drain()
            self._notify()
        except Exception:  # noqa: BLE001 – sniffing must never affect the proxy
            self.active = False
            self.buffer.clear()

    def _drain(self) -> None:
        while self.active:
            length, pos = _varint(self.buffer, 0)
            if length is None:
                return
            if length <= 0 or length > MAX_FRAME:
                self.active = False
                self.buffer.clear()
                return
            if len(self.buffer) < pos + length:
                return
            body = bytes(self.buffer[pos:pos + length])
            del self.buffer[:pos + length]
            self.frames += 1
            for candidate in _candidate_bodies(body):
                self._inspect(candidate)

    def _inspect(self, body: bytes) -> None:
        _, pos = _varint(body, 0)
        if pos >= len(body):
            return
        payload = body[pos:]

        info = parse_client_information(payload, self.protocol)
        if info is not None:
            self._set_client_info(info)
            return

        message = parse_plugin_message(payload)
        if message is not None:
            self._add_plugin_message(*message)

    def _set_client_info(self, info: dict) -> None:
        if info == self.client_info:
            return
        # A later settings change is meaningful, but do not let a structurally
        # valid-looking unrelated packet replace a complete capture.
        if self.client_info and info["locale"] == self.client_info["locale"]:
            for key in ("text_filtering", "allow_listings", "particle_status"):
                if info.get(key) is None and self.client_info.get(key) is not None:
                    info[key] = self.client_info[key]
        self.client_info = info
        self.captured_at = int(time.time())
        self._pending = True
        self._notify()

    def _add_plugin_message(self, channel: str, data: bytes) -> None:
        digest = hashlib.sha256(data).hexdigest()
        entry = self.plugin_messages.get(channel)
        if entry is None:
            if len(self.plugin_messages) >= MAX_PLUGIN_CHANNELS:
                return
            entry = {"channel": channel, "count": 0, "bytes": len(data), "sha256": digest}
            self.plugin_messages[channel] = entry
        else:
            entry["bytes"] = max(entry["bytes"], len(data))
            entry["sha256"] = digest
        entry["count"] += 1

        brand = _decode_brand(channel, data)
        if brand and brand != self.brand:
            self.brand = brand
        self.captured_at = int(time.time())
        self._pending = True
        self._notify()

    def _notify(self, force: bool = False) -> None:
        if self._on_capture is None or not self._pending:
            return
        now = time.monotonic()
        # Login bursts deliver many plugin messages at once. Coalesce them so
        # the proxy does not rewrite the fingerprint file for every packet.
        if not force and now - self._last_notify < NOTIFY_INTERVAL:
            return
        self._pending = False
        self._last_notify = now
        try:
            self._on_capture(self.snapshot())
        except Exception:  # noqa: BLE001
            pass

    def flush(self) -> None:
        """Persist a pending capture immediately (connection closed)."""
        self._notify(force=True)

    def snapshot(self) -> dict:
        return {
            "client_info": dict(self.client_info) if self.client_info else None,
            "plugin_messages": [
                dict(entry) for entry in sorted(self.plugin_messages.values(), key=lambda e: e["channel"].lower())
            ],
            "brand": self.brand,
            "frames": self.frames,
            "captured_at": self.captured_at or int(time.time()),
        }
