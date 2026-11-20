import gzip
from types import SimpleNamespace

from app import playerstats


def _inst(tmp_path):
    return SimpleNamespace(dir=tmp_path)


def test_deaths_are_counted_from_server_logs(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "latest.log").write_text(
        "[12:00:00] [Server thread/INFO]: Steve was slain by Zombie\n"
        "[12:00:01] [Server thread/INFO]: <Steve> I died, really\n"
        "[12:00:02] [Server thread/INFO]: Steve has made the advancement [Stone Age]\n"
        "[12:00:03] [Server thread/INFO]: Steve was kicked by an operator\n"
        "[12:00:04] [Server thread/INFO]: System chat: Alex drowned\n"
        "[12:00:05] [Server thread/INFO]: [Death] Herobrine died\n"
        "[12:00:06] [Server thread/INFO]: Notch fell from a high place\n"
        "[12:00:07] [Server thread/INFO]: Steve was killed by magic\n",
        encoding="utf-8",
    )
    known = {
        "steve": {"name": "Steve"},
        "alex": {"name": "Alex"},
        "herobrine": {"name": "Herobrine"},
    }
    counts = playerstats.death_counts(_inst(tmp_path), known, ["Notch"])
    assert counts == {"steve": 2, "alex": 1, "herobrine": 1, "notch": 1}


def test_latest_log_is_read_incrementally_and_rotations_are_included(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    latest = logs / "latest.log"
    latest.write_text("[12:00:00] [Server thread/INFO]: Steve was slain by Zombie\n", encoding="utf-8")
    known = {"steve": {"name": "Steve"}, "alex": {"name": "Alex"}}
    assert playerstats.death_counts(_inst(tmp_path), known, []) == {"steve": 1, "alex": 0}

    # A death is counted only once its line is complete.
    with latest.open("a", encoding="utf-8") as fh:
        fh.write("[12:00:01] [Server thread/INFO]: Steve drowned")
    assert playerstats.death_counts(_inst(tmp_path), known, [])["steve"] == 1
    with latest.open("a", encoding="utf-8") as fh:
        fh.write("\n")
    assert playerstats.death_counts(_inst(tmp_path), known, [])["steve"] == 2

    with gzip.open(logs / "2026-11-19-1.log.gz", "wt", encoding="utf-8") as fh:
        fh.write("[12:00:02] [Server thread/INFO]: Alex was blown up by Creeper\n")
    assert playerstats.death_counts(_inst(tmp_path), known, []) == {"steve": 2, "alex": 1}


def test_death_counts_without_logs_are_zero(tmp_path):
    assert playerstats.death_counts(_inst(tmp_path), {"steve": {"name": "Steve"}}, []) == {"steve": 0}
