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


def test_server_java_is_on_path_for_launchers(tmp_path, monkeypatch):
    """Launcher JARs (e.g. MythosCore) start `java` again by name – it must resolve to the panel's Java."""
    import os
    import shutil
    from app.manager import server_env
    java = tmp_path / "temurin-21" / "jdk-21" / "bin" / "java"
    java.parent.mkdir(parents=True)
    java.write_text("#!/bin/sh\n")
    java.chmod(0o755)
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    env = server_env(str(java))
    assert env["PATH"].split(os.pathsep)[0] == str(java.parent)
    assert env["JAVA_HOME"] == str(java.parent.parent)
    assert shutil.which("java", path=env["PATH"]) == str(java)
    assert env["TERM"] == "xterm-256color"
    # a bare command name (no bin/ folder) leaves PATH alone
    assert server_env("java")["PATH"] == "/usr/bin:/bin"


# ---------------------------------------------------------------- JVM flags (Aikar)

AIKAR_ARTICLE = (  # https://aikar.co/2018/07/02/tuning-the-jvm-g1gc-garbage-collector-flags-for-minecraft/
    "-XX:+UseG1GC -XX:+ParallelRefProcEnabled -XX:MaxGCPauseMillis=200 -XX:+UnlockExperimentalVMOptions "
    "-XX:+DisableExplicitGC -XX:+AlwaysPreTouch -XX:G1NewSizePercent=30 -XX:G1MaxNewSizePercent=40 "
    "-XX:G1HeapRegionSize=8M -XX:G1ReservePercent=20 -XX:G1HeapWastePercent=5 -XX:G1MixedGCCountTarget=4 "
    "-XX:InitiatingHeapOccupancyPercent=15 -XX:G1MixedGCLiveThresholdPercent=90 "
    "-XX:G1RSetUpdatingPauseTimePercent=5 -XX:SurvivorRatio=32 -XX:+PerfDisableSharedMem "
    "-XX:MaxTenuringThreshold=1 -Dusing.aikars.flags=https://mcflags.emc.gs -Daikars.new.flags=true"
).split()
AIKAR_OVER_12GB = {"-XX:G1NewSizePercent=40", "-XX:G1MaxNewSizePercent=50", "-XX:G1HeapRegionSize=16M",
                   "-XX:G1ReservePercent=15", "-XX:InitiatingHeapOccupancyPercent=20"}


def test_aikar_preset_is_exactly_the_published_flag_set():
    from app.manager import jvm_flags
    flags = jvm_flags("aikar", 10 * 1024, 21)
    assert set(flags) - {"-XX:+IgnoreUnrecognizedVMOptions"} == set(AIKAR_ARTICLE)
    big = set(jvm_flags("aikar", 16 * 1024, 21)) - {"-XX:+IgnoreUnrecognizedVMOptions"}
    small_only = {f for f in AIKAR_ARTICLE if f.split("=")[0] in {g.split("=")[0] for g in AIKAR_OVER_12GB}}
    assert big == (set(AIKAR_ARTICLE) - small_only) | AIKAR_OVER_12GB


def test_aikar_preset_uses_the_same_xms_and_xmx(tmp_path):
    jar(tmp_path, "server.jar")
    inst = make_inst(tmp_path, target="server.jar")
    inst.cfg.java.min_ram_mb, inst.cfg.java.max_ram_mb = 1024, 6144
    cmd = ServerInstance.build_command(inst, "java", 21)
    assert "-Xms6144M" in cmd and "-Xmx6144M" in cmd
    inst.cfg.java.flags_preset = "none"  # without Aikar's flags the start heap stays configurable
    cmd = ServerInstance.build_command(inst, "java", 21)
    assert "-Xms1024M" in cmd and "-Xmx6144M" in cmd
