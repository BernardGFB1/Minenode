import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app import api, playerstats
from app.models import PublicPageSettings

STEVE = "069a79f4-44e9-4726-a5be-fca90e38aaf5"
ALEX = "853c80ef-3c37-49fd-aa49-938b674adae6"


def _store(known=None):
    return SimpleNamespace(data={"known": known or {}, "attempts": {}}, save=lambda: None)


def _inst(tmp_path, mc_version="1.21.4", known=None):
    (tmp_path / "server.properties").write_text("level-name=world\n", encoding="utf-8")
    return SimpleNamespace(
        dir=tmp_path,
        cfg=SimpleNamespace(mc_version=mc_version, public_page=PublicPageSettings()),
        props=lambda: {"level-name": "world"},
        online_mode=True,
        players_store=_store(known),
        running=False,
        state="offline",
    )


def _write_stats(root, world, *, play=144000, deaths=4, mined=1200, player_uuid=STEVE, new_layout=False):
    folder = "players/stats" if new_layout else "stats"
    path = root / world / folder / f"{player_uuid}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "DataVersion": 4189,
        "stats": {
            "minecraft:custom": {
                "minecraft:play_time": play,
                "minecraft:deaths": deaths,
                "minecraft:mob_kills": 87,
                "minecraft:player_kills": 2,
                "minecraft:jump": 1940,
                "minecraft:walk_one_cm": 542000,
                "minecraft:sprint_one_cm": 310000,
                "minecraft:fly_one_cm": 120000,
            },
            "minecraft:mined": {
                "minecraft:stone": mined,
                "minecraft:diamond_ore": 14,
            },
            "minecraft:crafted": {"minecraft:torch": 256, "create:andesite_alloy": 64},
            "minecraft:killed": {"minecraft:zombie": 40, "minecraft:creeper": 12},
            "minecraft:killed_by": {"minecraft:skeleton": 2},
        },
    }, ensure_ascii=False), encoding="utf-8")
    return path


def test_world_stats_exist_only_since_1_13():
    def version(value):
        return SimpleNamespace(cfg=SimpleNamespace(mc_version=value))

    assert playerstats.supported(version("1.13"))
    assert playerstats.supported(version("1.21.4"))
    assert playerstats.supported(version("26.1"))
    assert not playerstats.supported(version("1.12.2"))
    assert not playerstats.supported(version("1.8.9"))
    # Snapshots have no numeric version in their name – they are modern enough.
    assert playerstats.supported(version("24w14a"))


def test_player_stats_are_normalized_from_the_world_file(tmp_path):
    inst = _inst(tmp_path, known={"steve": {"name": "Steve", "uuid": STEVE, "last_seen": 1000}})
    _write_stats(tmp_path, "world")

    data = playerstats.player_stats(inst, STEVE)

    assert data["uuid"] == STEVE and data["name"] == "Steve"
    assert data["play_time_seconds"] == 7200
    assert data["play_time_hours"] == 2
    assert data["deaths"] == 4 and data["mob_kills"] == 87 and data["player_kills"] == 2
    assert data["jumps"] == 1940
    assert data["distance_km"] == 9.72  # 972000 cm
    assert data["blocks_mined"] == 1214
    assert data["items_crafted"] == 320
    assert data["top"]["mined"][0] == {"id": "minecraft:stone", "count": 1200}
    assert data["top"]["killed"][0]["id"] == "minecraft:zombie"
    assert data["last_seen"] == 1000


def test_statistics_from_every_world_are_summed(tmp_path):
    inst = _inst(tmp_path, known={"steve": {"name": "Steve", "uuid": STEVE}})
    _write_stats(tmp_path, "world", play=1000, mined=10)
    _write_stats(tmp_path, "world_nether", play=2000, mined=20)

    data = playerstats.player_stats(inst, STEVE)

    assert data["play_time_seconds"] == 150  # 3000 ticks
    assert data["blocks_mined"] == 58  # (10 + 14) + (20 + 14)


def test_player_stats_unknown_player_returns_none(tmp_path):
    inst = _inst(tmp_path)
    _write_stats(tmp_path, "world")
    assert playerstats.player_stats(inst, ALEX) is None
    assert playerstats.player_stats(inst, "not-a-uuid") is None


def test_player_stats_are_read_from_the_26_1_players_folder(tmp_path):
    """Since Minecraft 26.1 player files live under <world>/players/stats."""
    inst = _inst(tmp_path, mc_version="26.1", known={"steve": {"name": "Steve", "uuid": STEVE}})
    _write_stats(tmp_path, "world", new_layout=True)

    data = playerstats.player_stats(inst, STEVE)

    assert data and data["name"] == "Steve"
    assert data["play_time_hours"] == 2


def test_migrated_world_is_not_counted_twice(tmp_path):
    """A world migrated from the old layout must not sum the same stats twice."""
    inst = _inst(tmp_path, mc_version="26.1", known={"steve": {"name": "Steve", "uuid": STEVE}})
    _write_stats(tmp_path, "world", play=1000, mined=10, new_layout=True)
    _write_stats(tmp_path, "world", play=999999, mined=999, new_layout=False)  # leftover

    data = playerstats.player_stats(inst, STEVE)

    assert data["play_time_seconds"] == 50  # only the players/stats file is used
    assert data["blocks_mined"] == 24


def test_sync_known_imports_players_from_stats_and_usercache(tmp_path):
    inst = _inst(tmp_path)
    stats = _write_stats(tmp_path, "world")
    (tmp_path / "usercache.json").write_text(json.dumps([
        {"name": "Steve", "uuid": STEVE, "expiresOn": "2026-12-01 00:00:00 +0000"},
    ]), encoding="utf-8")

    playerstats.sync_known(inst)

    entry = inst.players_store.data["known"]["steve"]
    assert entry["name"] == "Steve" and entry["uuid"] == STEVE
    assert entry["last_seen"] == pytest.approx(stats.stat().st_mtime, abs=2)
    # Running it again must not duplicate or change anything.
    before = dict(inst.players_store.data["known"])
    playerstats.sync_known(inst)
    assert inst.players_store.data["known"] == before


def test_sync_known_recovers_missing_names_from_logs(tmp_path):
    inst = _inst(tmp_path)
    _write_stats(tmp_path, "world")
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "latest.log").write_text(
        "[12:00:00] [Server thread/INFO]: UUID of player Steve is 069a79f444e94726a5befca90e38aaf5\n",
        encoding="utf-8")

    playerstats.sync_known(inst)

    assert inst.players_store.data["known"]["steve"]["uuid"] == STEVE


def test_sync_known_keeps_one_entry_per_uuid_after_a_nick_change(tmp_path):
    old = "7f2c0d9e-0000-0000-0000-000000000001"
    inst = _inst(tmp_path, known={"oldname": {"name": "OldName", "uuid": old, "last_seen": 500}})
    _write_stats(tmp_path, "world", player_uuid=old)
    (tmp_path / "usercache.json").write_text(json.dumps([{"name": "NewName", "uuid": old}]),
                                             encoding="utf-8")

    playerstats.sync_known(inst)

    assert list(inst.players_store.data["known"]) == ["oldname"]
    assert inst.players_store.data["known"]["oldname"]["last_seen"] == 500


def test_sync_known_never_shrinks_last_seen_when_merging(tmp_path):
    uid = "7f2c0d9e-0000-0000-0000-000000000002"
    inst = _inst(tmp_path, known={
        "oldname": {"name": "OldName", "uuid": uid, "last_seen": 5000},
        "newname": {"name": "NewName", "uuid": uid, "last_seen": 100},
    })
    _write_stats(tmp_path, "world", player_uuid=uid)

    playerstats.sync_known(inst)

    known = inst.players_store.data["known"]
    assert len(known) == 1
    entry = next(iter(known.values()))
    assert entry["last_seen"] == 5000  # a dict.update would have overwritten it with 100


def _api_instance(tmp_path, *, player_stats=True, mc_version="1.21.4"):
    known = {"steve": {"name": "Steve", "uuid": STEVE, "last_seen": 1000}}
    inst = _inst(tmp_path, mc_version=mc_version, known=known)
    inst.cfg.public_page = PublicPageSettings(player_stats=player_stats)
    return inst


def test_public_player_stats_respect_the_toggle(monkeypatch, tmp_path):
    inst = _api_instance(tmp_path, player_stats=False)
    monkeypatch.setattr(api, "_public_inst", lambda key: inst)
    response = asyncio.run(api.public_player_stats("survival", STEVE))
    assert response.status_code == 403


def test_public_player_stats_need_minecraft_1_13(monkeypatch, tmp_path):
    inst = _api_instance(tmp_path, mc_version="1.12.2")
    monkeypatch.setattr(api, "_public_inst", lambda key: inst)
    response = asyncio.run(api.public_player_stats("survival", STEVE))
    assert response.status_code == 400


def test_public_player_stats_reject_invalid_uuid(monkeypatch, tmp_path):
    inst = _api_instance(tmp_path)
    monkeypatch.setattr(api, "_public_inst", lambda key: inst)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(api.public_player_stats("survival", "../../secret"))
    assert exc.value.status_code == 404


def test_public_player_stats_return_the_world_file(monkeypatch, tmp_path):
    inst = _api_instance(tmp_path)
    _write_stats(tmp_path, "world")
    monkeypatch.setattr(api, "_public_inst", lambda key: inst)

    response = asyncio.run(api.public_player_stats("survival", STEVE))

    assert response.status_code == 200
    body = json.loads(response.body)
    assert body["name"] == "Steve" and body["play_time_hours"] == 2
    assert body["top"]["mined"][0]["count"] == 1200


def test_public_player_stats_404_without_a_file(monkeypatch, tmp_path):
    inst = _api_instance(tmp_path)
    monkeypatch.setattr(api, "_public_inst", lambda key: inst)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(api.public_player_stats("survival", STEVE))
    assert exc.value.status_code == 404
