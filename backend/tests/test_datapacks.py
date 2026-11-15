import json
import zipfile
from types import SimpleNamespace

import pytest

from app import datapacks


def make_inst(tmp_path):
    logs = []
    (tmp_path / "server.properties").write_text("level-name=swiat\n")
    return SimpleNamespace(dir=tmp_path, cfg=SimpleNamespace(mc_version="1.21.4"), panel_log=logs.append), logs


def make_zip(path, files):
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return path


MCMETA = json.dumps({"pack": {"pack_format": 61, "description": {"text": "Lepsze ", "extra": ["biomy"]}}})


def test_upload_lands_in_the_world_and_is_listed(tmp_path):
    inst, _ = make_inst(tmp_path)
    up = make_zip(tmp_path / "up.zip", {"pack.mcmeta": MCMETA, "data/x/a.json": "{}", "pack.png": b"PNG"})
    p = datapacks.add_file(inst, up, "Terralith 2.5.zip")
    assert (tmp_path / "swiat" / "datapacks" / "Terralith 2.5.zip").is_file() and not up.exists()
    assert p["enabled"] and p["description"] == "Lepsze biomy" and p["pack_format"] == 61 and p["has_icon"]


def test_wrapped_folder_is_fixed_and_resource_packs_or_jars_are_refused(tmp_path):
    inst, _ = make_inst(tmp_path)
    wrapped = make_zip(tmp_path / "w.zip", {"Pack/pack.mcmeta": MCMETA, "Pack/data/x/a.json": "{}"})
    assert datapacks.add_file(inst, wrapped, "w.zip")["fixed"]
    rp = make_zip(tmp_path / "rp.zip", {"pack.mcmeta": MCMETA, "assets/x.png": "x"})
    with pytest.raises(datapacks.DatapackError, match="paczka zasobów"):
        datapacks.add_file(inst, rp, "rp.zip")
    jar = make_zip(tmp_path / "p.jar", {"plugin.yml": "name: X\n"})
    with pytest.raises(datapacks.DatapackError, match="plugin"):
        datapacks.add_file(inst, jar, "p.jar")
    assert not rp.exists() and not jar.exists()  # temp files never stay behind


def test_disable_moves_out_of_the_world_and_back(tmp_path):
    inst, _ = make_inst(tmp_path)
    datapacks.add_file(inst, make_zip(tmp_path / "a.zip", {"pack.mcmeta": MCMETA, "data/a": "x"}), "a.zip")
    folder = tmp_path / "swiat" / "datapacks" / "folderpack"
    (folder / "data").mkdir(parents=True)
    (folder / "pack.mcmeta").write_text(MCMETA)
    datapacks.toggle(inst, "a.zip", False)
    datapacks.toggle(inst, "folderpack", False)
    assert not (tmp_path / "swiat" / "datapacks" / "a.zip").exists()
    assert {p["file"]: p["enabled"] for p in datapacks.list_packs(inst)} == {"a.zip": False, "folderpack": False}
    datapacks.toggle(inst, "a.zip", True)
    assert (tmp_path / "swiat" / "datapacks" / "a.zip").is_file()
    datapacks.remove(inst, "folderpack")
    assert [p["file"] for p in datapacks.list_packs(inst)] == ["a.zip"]


@pytest.mark.parametrize("bad", ["../server.properties", "a/b.zip", "..", ".minenode", ""])
def test_names_cannot_escape_the_folder(tmp_path, bad):
    inst, _ = make_inst(tmp_path)
    with pytest.raises(datapacks.DatapackError):
        datapacks.remove(inst, bad)
