"""Packet framing/parsing tests for the informational client fingerprint."""
import asyncio
import ipaddress
import struct
import zlib

import pytest

from app.fingerprint import PacketSniffer, parse_client_information, parse_plugin_message
from app.proxy import PROXY_V2_SIG, ProtocolError, _PrefixedReader, _is_loopback_peer, MinecraftProxy


def varint(value: int) -> bytes:
    value &= 0xFFFFFFFF
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        out.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(out)


def mc_string(value: str) -> bytes:
    data = value.encode("utf-8")
    return varint(len(data)) + data


def client_info(appended: bytes = bytes([0, 1])) -> bytes:
    # locale, view distance, chat mode, chat colours, skin parts, main hand,
    # text filtering, server listings (the last two are absent on old clients).
    return mc_string("en_us") + bytes([12, 0, 1, 0x7F]) + varint(0) + appended


def frame(body: bytes) -> bytes:
    return varint(len(body)) + body


def test_parse_client_information_for_current_and_legacy_clients():
    current = parse_client_information(client_info())
    assert current == {
        "locale": "en_us",
        "view_distance": 12,
        "chat_mode": 0,
        "chat_colors": True,
        "skin_parts": 0x7F,
        "skin_parts_enabled": ["cape", "jacket", "left_sleeve", "right_sleeve",
                               "left_pants", "right_pants", "hat"],
        "main_hand": 0,
        "text_filtering": False,
        "allow_listings": True,
        "particle_status": None,
    }
    legacy = parse_client_information(client_info(b""))
    assert legacy["locale"] == "en_us"
    assert legacy["text_filtering"] is None
    assert legacy["allow_listings"] is None


def test_parse_client_information_for_1_7_and_1_8_clients():
    # 1.8: locale, view distance, chat mode, colours, skin parts – no main hand.
    v18 = mc_string("pl_pl") + bytes([8, 0, 1, 0x02])
    parsed = parse_client_information(v18, protocol=47)
    assert parsed["main_hand"] is None
    assert parsed["skin_parts_enabled"] == ["jacket"]

    # 1.7: chat flags (visibility + colour bit), difficulty, show cape.
    v17 = mc_string("de_de") + bytes([6, 2 | 4, 2, 1])
    parsed = parse_client_information(v17, protocol=5)
    assert parsed["chat_mode"] == 2
    assert parsed["chat_colors"] is True
    assert parsed["skin_parts_enabled"] == ["cape"]
    assert parsed["main_hand"] is None
    assert parse_client_information(v17 + b"\x00", protocol=5) is None


def test_parse_plugin_message_requires_a_channel_and_matching_payload_length():
    brand = mc_string("vanilla")
    payload = mc_string("minecraft:brand") + varint(len(brand)) + brand
    assert parse_plugin_message(payload) == ("minecraft:brand", brand)
    broken = mc_string("minecraft:brand") + varint(99) + brand
    assert parse_plugin_message(broken) is None
    assert parse_plugin_message(mc_string("en_us") + varint(0)) is None


def test_sniffer_understands_plain_and_compressed_frames():
    plain = PacketSniffer(769)
    plain.feed(frame(varint(0) + client_info()))
    assert plain.client_info["locale"] == "en_us"

    body = varint(0) + client_info()
    compressed_body = varint(len(body)) + zlib.compress(body)
    compressed = PacketSniffer(769)
    compressed.feed(frame(compressed_body))
    assert compressed.client_info["locale"] == "en_us"

    brand = mc_string("vanilla")
    plugin_body = varint(1) + mc_string("minecraft:brand") + varint(len(brand)) + brand
    plugin = PacketSniffer(769)
    plugin.feed(frame(plugin_body))
    assert plugin.brand == "vanilla"
    assert plugin.plugin_messages["minecraft:brand"]["count"] == 1


def test_sniffer_survives_malformed_and_split_frames():
    sniffer = PacketSniffer()
    good = frame(varint(0) + client_info())
    sniffer.feed(good[:3])
    assert sniffer.client_info is None
    sniffer.feed(good[3:])
    assert sniffer.client_info["locale"] == "en_us"

    bad = PacketSniffer()
    bad.feed(varint(50) + b"\x00" * 50)  # complete garbage frame
    bad.feed(frame(varint(0) + client_info()))
    assert bad.client_info["locale"] == "en_us"


def test_sniffer_coalesces_capture_writes_and_flushes_on_close(monkeypatch):
    from app import fingerprint

    now = [100.0]
    monkeypatch.setattr(fingerprint.time, "monotonic", lambda: now[0])
    snapshots = []
    sniffer = PacketSniffer(769, on_capture=snapshots.append)
    data = mc_string("vanilla")
    for _ in range(20):
        payload = varint(1) + mc_string("minecraft:brand") + varint(len(data)) + data
        sniffer.feed(frame(payload))
        now[0] += 0.05

    assert len(snapshots) == 1  # login burst collapsed into one write
    assert sniffer.plugin_messages["minecraft:brand"]["count"] == 20
    sniffer.flush()
    assert len(snapshots) == 2
    assert snapshots[-1]["plugin_messages"][0]["count"] == 20


class _Writer:
    def __init__(self):
        self.data = bytearray()

    def write(self, data):
        self.data += data

    async def drain(self):
        return None


def test_proxy_v2_header_is_read_and_only_trusted_from_loopback():
    async def read(header: bytes, peer=("127.0.0.1", 50000)):
        reader = asyncio.StreamReader()
        reader.feed_data(header + b"\x10")
        reader.feed_eof()
        return await MinecraftProxy._read_inbound_prefix(MinecraftProxy.__new__(MinecraftProxy), reader, peer)

    source = ("203.0.113.7", 12345)
    ipv4 = PROXY_V2_SIG + bytes([0x21, 0x11]) + struct.pack("!H", 12) + \
        bytes([203, 0, 113, 7, 10, 0, 0, 1]) + struct.pack("!HH", 12345, 25565)
    first, peer, reader = asyncio.run(read(ipv4))
    assert first == b"\x10" and peer == source
    assert asyncio.run(reader.readexactly(0)) == b""

    # IPv4-mapped IPv6 source addresses are shown in their plain IPv4 form.
    payload = (ipaddress.IPv6Address("::ffff:203.0.113.7").packed
               + ipaddress.IPv6Address("::1").packed
               + struct.pack("!HH", 12345, 25565))
    ipv6 = PROXY_V2_SIG + bytes([0x21, 0x21]) + struct.pack("!H", 36) + payload
    first, peer, _ = asyncio.run(read(ipv6))
    assert first == b"\x10" and peer == source

    # A handshake can start with 0x0d; the bytes must be replayed, not eaten.
    handshake = b"\r" + b"abcdefghijk"
    first, peer, reader = asyncio.run(read(handshake))
    assert first == b"\r"
    assert asyncio.run(reader.readexactly(11)) == b"abcdefghijk"

    with pytest.raises(ProtocolError):
        asyncio.run(read(ipv4, ("198.51.100.9", 50000)))
    assert _is_loopback_peer(("::1", 1, 0, 0))
    assert _is_loopback_peer(("::ffff:127.0.0.1", 1, 0, 0))
    assert not _is_loopback_peer(("203.0.113.7", 1))


def test_prefixed_reader_replays_bytes_in_order():
    async def scenario():
        reader = asyncio.StreamReader()
        reader.feed_data(b"world")
        reader.feed_eof()
        wrapper = _PrefixedReader(reader, b"hello ")
        assert await wrapper.readexactly(5) == b"hello"
        assert await wrapper.read(2) == b" w"
        assert await wrapper.read() == b"orld"

    asyncio.run(scenario())
