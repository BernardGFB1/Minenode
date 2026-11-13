import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import content, providers
from app.manager import ServerError, ServerInstance
from app.models import LaunchSpec, ServerConfig


def make_inst(tmp_path, target=""):
    cfg = ServerConfig(id="c1", name="Custom", type="custom", mc_version="1.20.1", subdomain="custom",
                       internal_port=25600, launch=LaunchSpec(kind="jar", target=target), installed=True)
    inst = SimpleNamespace(dir=tmp_path, cfg=cfg)
    inst.custom_jars = lambda: ServerInstance.custom_jars(inst)
    inst.custom_jar = lambda: ServerInstance.custom_jar(inst)
    return inst


def jar(tmp_path, name):
    (tmp_path / name).write_bytes(b"PK")


def test_engine_is_listed_without_mods_or_plugins():
    assert providers.ENGINES["custom"]["custom"] is True
    assert content.kind_for("custom") is None
    assert content.LOADER_COMPAT["custom"] == []


def test_install_downloads_nothing_and_keeps_the_users_jar(tmp_path):
    jar(tmp_path, "magma-1.20.1.jar")
    logs = []
    ctx = providers.InstallContext(tmp_path, "1.20.1", None, Path("java"), logs.append)
    launch, loader = asyncio.run(providers.install("custom", ctx))
    assert launch.kind == "jar" and launch.target == "" and loader is None
    assert [p.name for p in tmp_path.iterdir()] == ["magma-1.20.1.jar"]
    assert any("magma-1.20.1.jar" in line for line in logs)


def test_missing_jar_gives_a_clear_error(tmp_path):
    with pytest.raises(ServerError, match="Wgraj plik .jar"):
        make_inst(tmp_path).custom_jar()


def test_single_jar_is_detected_whatever_its_name(tmp_path):
    jar(tmp_path, "MyEngine-2.3.jar")
    (tmp_path / "libraries").mkdir()
    jar(tmp_path / "libraries", "not-this-one.jar")  # only the root folder counts
    inst = make_inst(tmp_path)
    assert inst.custom_jar() == "MyEngine-2.3.jar"
    cmd = ServerInstance.build_command(inst, "java", 17)
    assert cmd[cmd.index("-jar") + 1] == "MyEngine-2.3.jar"
    assert cmd[-1] == "nogui"


def test_renamed_jar_is_picked_up_on_next_start(tmp_path):
    jar(tmp_path, "old.jar")
    inst = make_inst(tmp_path, target="old.jar")
    (tmp_path / "old.jar").rename(tmp_path / "new.jar")
    assert inst.custom_jar() == "new.jar"


def test_several_jars_need_a_choice(tmp_path):
    jar(tmp_path, "a.jar")
    jar(tmp_path, "b.jar")
    with pytest.raises(ServerError, match="kilka plików .jar"):
        make_inst(tmp_path).custom_jar()
    assert make_inst(tmp_path, target="b.jar").custom_jar() == "b.jar"


def test_create_and_reinstall_requests_accept_the_custom_engine():
    from app import api
    assert api.CreateServer(name="Magma", type="custom", mc_version="1.20.1").type == "custom"
    for engine in providers.ENGINES:  # every engine in the wizard must be accepted by the API
        api.CreateServer(name="x", type=engine, mc_version="1.20.1")
