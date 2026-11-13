"""Bedrock gateway tests (stdlib unittest – the project image has no pytest)."""
from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app import bedrock


class FakeInst:
    def __init__(self, sid: str, name: str, subdomain: str, *, aliases=(), online=True, enabled=True):
        self.cfg = SimpleNamespace(
            id=sid,
            name=name,
            subdomain=subdomain,
            aliases=list(aliases),
            bedrock_enabled=enabled,
        )
        self.online_mode = online


class FakeManager:
    def __init__(self, instances, *, base_domain="", public_address="", default_server_id=None,
                 bedrock_advertise=""):
        self.instances = {inst.cfg.id: inst for inst in instances}
        self.settings = SimpleNamespace(
            base_domain=base_domain,
            public_address=public_address,
            default_server_id=default_server_id,
            bedrock_advertise=bedrock_advertise,
        )


def make_proxy(instances, **settings) -> bedrock.BedrockProxy:
    return bedrock.BedrockProxy(FakeManager(instances, **settings), "0.0.0.0", 19132)


class RouteTableTests(unittest.TestCase):
    def test_normalize_host(self):
        self.assertEqual(bedrock.normalize_host("  Play.Example.COM.  "), "play.example.com")
        self.assertEqual(bedrock.normalize_host("play.example.com:19132"), "play.example.com")
        self.assertEqual(bedrock.normalize_host("http://play.example.com:19132"), "play.example.com")
        self.assertEqual(bedrock.normalize_host("https://play.example.com/path"), "play.example.com")
        self.assertEqual(bedrock.normalize_host("[2001:db8::1]:19132"), "2001:db8::1")
        self.assertEqual(bedrock.normalize_host("2001:db8::1"), "2001:db8::1")
        self.assertEqual(bedrock.normalize_host(None), "")

    def test_valid_host(self):
        self.assertTrue(bedrock.valid_host("play.example.com"))
        self.assertTrue(bedrock.valid_host("localhost"))
        self.assertTrue(bedrock.valid_host("192.0.2.1"))
        self.assertTrue(bedrock.valid_host("2001:db8::1"))
        self.assertFalse(bedrock.valid_host(""))
        self.assertFalse(bedrock.valid_host("bad host"))
        self.assertFalse(bedrock.valid_host("*.example.com"))
        self.assertFalse(bedrock.valid_host("-bad.example.com"))

    def test_routes_aliases_subdomains_and_root(self):
        a = FakeInst("a", "Survival", "survival", aliases=["play.example.com"], online=True)
        b = FakeInst("b", "Creative", "creative", online=False)
        proxy = make_proxy([a, b], base_domain="mc.example.com", default_server_id="a")
        routes = proxy.route_map()

        self.assertEqual(routes["play.example.com"], a)
        self.assertEqual(routes["survival.mc.example.com"], a)
        self.assertEqual(routes["creative.mc.example.com"], b)
        self.assertEqual(routes["mc.example.com"], a)  # bare root -> default server
        self.assertEqual(proxy.hosts_for(a), ["play.example.com", "survival.mc.example.com", "mc.example.com"])
        self.assertEqual(proxy.public_host(b), "creative.mc.example.com")

    def test_no_base_domain_uses_subdomain_public_hostname(self):
        a = FakeInst("a", "Survival", "survival")
        proxy = make_proxy([a], public_address="example.com", default_server_id="a")
        self.assertEqual(proxy.route_map(), {
            "survival.example.com": a,
            "example.com": a,
        })

    def test_public_ip_only_gets_root_route(self):
        a = FakeInst("a", "Survival", "survival")
        proxy = make_proxy([a], public_address="192.0.2.10", default_server_id="a")
        self.assertEqual(proxy.route_map(), {"192.0.2.10": a})

    def test_disabled_servers_are_not_routed(self):
        a = FakeInst("a", "Survival", "survival", enabled=False)
        proxy = make_proxy([a], base_domain="example.com", default_server_id="a")
        self.assertEqual(proxy.route_map(), {})
        self.assertEqual(proxy.hosts_for(a), [])

    def test_aliases_win_over_other_servers_subdomain(self):
        a = FakeInst("a", "Alias owner", "other", aliases=["creative.example.com"])
        b = FakeInst("b", "Creative", "creative")
        proxy = make_proxy([a, b], base_domain="example.com", default_server_id="a")
        routes = proxy.route_map()
        self.assertIs(routes["creative.example.com"], a)
        self.assertNotIn("creative.example.com", proxy.hosts_for(b))
        self.assertTrue(any("creative.example.com" in conflict for conflict in proxy._conflicts))

    def test_no_root_route_when_default_is_not_bedrock_enabled(self):
        a = FakeInst("a", "Disabled default", "one", enabled=False)
        b = FakeInst("b", "Second", "two")
        c = FakeInst("c", "Third", "three")
        proxy = make_proxy([a, b, c], base_domain="example.com", default_server_id="a")
        routes = proxy.route_map()
        self.assertNotIn("example.com", routes)
        self.assertEqual(routes["two.example.com"], b)
        self.assertEqual(routes["three.example.com"], c)

    def test_single_enabled_server_gets_root_without_default(self):
        a = FakeInst("a", "Only", "only")
        proxy = make_proxy([a], base_domain="example.com")
        self.assertIs(proxy.route_map()["example.com"], a)

    def test_disabled_default_keeps_root_unavailable(self):
        a = FakeInst("a", "Disabled default", "one", enabled=False)
        b = FakeInst("b", "Only Bedrock", "two")
        proxy = make_proxy([a, b], base_domain="example.com", default_server_id="a")
        routes = proxy.route_map()
        self.assertNotIn("example.com", routes)
        self.assertEqual(routes, {"two.example.com": b})

    def test_disabled_server_hostnames_are_reserved(self):
        # An alias belonging to a Bedrock-disabled server must not be silently reassigned.
        a = FakeInst("a", "Java only", "creative", aliases=["shared.example.com"], enabled=False)
        b = FakeInst("b", "Bedrock", "shared", enabled=True)
        proxy = make_proxy([a, b], base_domain="example.com", default_server_id="b")
        routes = proxy.route_map()
        self.assertNotIn("shared.example.com", routes)
        self.assertEqual(routes, {"example.com": b})
        self.assertTrue(any("shared.example.com" in conflict for conflict in proxy._conflicts))

    def test_routes_text_contains_auth_and_proxy_target(self):
        a = FakeInst("a", "Online", "online", aliases=["online.example.com"], online=True)
        b = FakeInst("b", "Offline", "offline", aliases=["offline.example.com"], online=False)
        proxy = make_proxy([a, b], base_domain="example.com", default_server_id="a")
        text, routes = proxy._routes_text()
        self.assertIn("denied-message=", text)
        self.assertIn("route.0=online.example.com|a|online|127.0.0.1|25565", text)
        self.assertIn("route.1=offline.example.com|b|offline|127.0.0.1|25565", text)
        self.assertIn("route.2=example.com|a|online|127.0.0.1|25565", text)
        # Aliases coincide with the subdomain-derived hosts, so there are three unique hosts.
        self.assertEqual(len(routes), 3)

    def test_disabled_hosts_are_denied_and_default_is_fallback(self):
        a = FakeInst("a", "A", "survival")
        b = FakeInst("b", "B", "creative", enabled=False, aliases=["play.other.net"])
        text, _ = make_proxy([a, b], base_domain="mc.example.com", default_server_id="a")._routes_text()
        self.assertIn("deny.0=creative.mc.example.com", text)
        self.assertIn("deny.1=play.other.net", text)
        self.assertNotIn("deny.2", text)  # enabled hosts are never denied
        self.assertIn("fallback=a|online|127.0.0.1|", text)

    def test_no_fallback_when_default_has_bedrock_off(self):
        a = FakeInst("a", "A", "survival", enabled=False)
        b = FakeInst("b", "B", "creative")
        text, _ = make_proxy([a, b], base_domain="mc.example.com", default_server_id="a")._routes_text()
        self.assertNotIn("fallback=", text)

    def test_advertise_prefers_settings_then_public_address(self):
        a = FakeInst("a", "A", "survival")
        p = make_proxy([a], public_address="79.163.180.236", bedrock_advertise="play.example.net:19133, 10.0.0.5")
        self.assertEqual(p.advertise_addresses(), (["play.example.net:19133", "10.0.0.5"], "settings"))
        self.assertIn("advertise-addresses: ['play.example.net:19133', '10.0.0.5']", p._config_text("online"))
        p = make_proxy([a], public_address="79.163.180.236")
        self.assertEqual(p.advertise_addresses(), (["79.163.180.236"], "public_address"))
        p = make_proxy([a], public_address="2001:db8::1")
        self.assertEqual(p.advertise_addresses()[0], ["[2001:db8::1]"])

    def test_advertise_uses_resolvable_hostname_else_stun(self):
        a = FakeInst("a", "A", "survival")
        p = make_proxy([a], base_domain="mc.example.com")
        with patch.object(bedrock, "_resolves", return_value=True):
            self.assertEqual(p.advertise_addresses(), (["survival.mc.example.com"], "hostname"))
        with patch.object(bedrock, "_resolves", return_value=False):
            self.assertEqual(p.advertise_addresses(), ([], "stun"))
            self.assertIn("advertise-addresses: []", p._config_text("online"))

    def test_advertise_lists_every_bedrock_server_address(self):
        # Each card must show its own player address as the game connection address.
        a = FakeInst("a", "Survival", "survival")
        b = FakeInst("b", "BMC", "bmc")
        off = FakeInst("c", "Off", "aaa", enabled=False)
        p = make_proxy([a, b, off], base_domain="zzweb.ovh", default_server_id="b")
        with patch.object(bedrock, "_resolves", return_value=True):
            hosts, source = p.advertise_addresses()
        self.assertEqual(source, "hostname")
        self.assertEqual(hosts, ["bmc.zzweb.ovh", "survival.zzweb.ovh"])  # default server first, no disabled one
        for inst in (a, b):
            self.assertIn(p.public_host(inst), hosts)
        with patch.object(bedrock, "_resolves", side_effect=lambda h: h != "bmc.zzweb.ovh"):
            self.assertEqual(p.advertise_addresses(), (["survival.zzweb.ovh"], "hostname"))

    def test_public_domain_without_dns_record_is_not_advertised(self):
        a = FakeInst("a", "Survival", "survival")
        p = make_proxy([a], public_address="zzweb.ovh")
        # *.zzweb.ovh resolves, the bare domain does not – Geyser would refuse to start signaling
        with patch.object(bedrock, "_resolves", side_effect=lambda h: h != "zzweb.ovh"):
            self.assertEqual(p.advertise_addresses(), (["survival.zzweb.ovh"], "hostname"))
        with patch.object(bedrock, "_resolves", return_value=True):
            self.assertEqual(p.advertise_addresses(), (["zzweb.ovh"], "public_address"))

    def test_resolve_ips(self):
        self.assertEqual(bedrock.resolve_ips("79.163.180.236"), frozenset({"79.163.180.236"}))
        self.assertEqual(bedrock.resolve_ips("2001:db8::1"), frozenset({"2001:db8::1"}))
        with patch("socket.getaddrinfo", side_effect=OSError):
            bedrock._resolve_cache.pop("nothing.invalid", None)
            self.assertEqual(bedrock.resolve_ips("nothing.invalid"), frozenset())
            self.assertFalse(bedrock._resolves("nothing.invalid"))

    def test_yaml_and_property_escaping(self):
        self.assertEqual(bedrock._yaml("it's"), "'it''s'")
        self.assertEqual(bedrock._escape_property("a\\b\nc"), "a\\\\b\\nc")

    def test_config_text(self):
        proxy = make_proxy([FakeInst("a", "Online", "online", online=True)], base_domain="example.com")
        text = proxy._config_text()
        self.assertIn("  address: '0.0.0.0'", text)
        self.assertIn("  port: 19132", text)
        self.assertIn("  webrtc-port: 0", text)
        self.assertIn("  transport: nethernet", text)
        self.assertIn("    mode: builtin", text)
        self.assertIn("  address: '127.0.0.1'", text)
        self.assertIn("  port: 25565", text)
        self.assertIn("  auth-type: online", text)
        self.assertIn("  forward-hostname: true", text)
        self.assertIn("advanced:", text)
        self.assertIn("    use-haproxy-protocol: true", text)
        self.assertIn("saved-user-logins: []", text)
        self.assertIn("log-player-ip-addresses: false", text)

    def test_global_auth_is_offline_only_when_every_enabled_server_is_offline(self):
        offline = FakeInst("a", "Offline", "offline", online=False)
        disabled_online = FakeInst("b", "Disabled online", "premium", online=True, enabled=False)
        proxy = make_proxy([offline, disabled_online], base_domain="example.com")
        self.assertEqual(proxy.auth_mode(), "offline")
        self.assertIn("  auth-type: offline", proxy._config_text())

        disabled_online.cfg.bedrock_enabled = True
        self.assertEqual(proxy.auth_mode(), "online")
        self.assertIn("  auth-type: online", proxy._config_text())


class FileHelpersTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_write_if_changed_is_atomic_and_skips_equal_content(self):
        path = self.tmp / "nested" / "routes.properties"
        self.assertTrue(bedrock._write_if_changed(path, "one"))
        self.assertFalse(bedrock._write_if_changed(path, "one"))
        self.assertTrue(bedrock._write_if_changed(path, "two"))
        self.assertEqual(path.read_text(encoding="utf-8"), "two")
        self.assertEqual(list(path.parent.glob("*.tmp")), [])


class ExtensionArtifactTests(unittest.TestCase):
    ROOT = Path(__file__).resolve().parents[1]
    JAR = ROOT / "bedrock_ext" / "MineNodeRouter.jar"

    def test_jar_contains_extension_metadata_and_classes(self):
        self.assertTrue(self.JAR.is_file(), "MineNodeRouter.jar must be committed")
        with zipfile.ZipFile(self.JAR) as zf:
            names = set(zf.namelist())
            self.assertIn("extension.yml", names)
            self.assertIn("pl/minenode/router/MineNodeRouter.class", names)
            self.assertIn("pl/minenode/router/RouteTable.class", names)
            self.assertIn("pl/minenode/router/RouteRemoteServer.class", names)
            metadata = zf.read("extension.yml").decode()
            self.assertIn("id: minenode-router", metadata)
            self.assertIn("main: pl.minenode.router.MineNodeRouter", metadata)
            self.assertIn("api: 2.9.2", metadata)

            class_bytes = zf.read("pl/minenode/router/MineNodeRouter.class")
            # Java class file: CAFEBABE, minor, major (65 = Java 21).
            self.assertEqual(class_bytes[:4], b"\xca\xfe\xba\xbe")
            self.assertEqual(int.from_bytes(class_bytes[6:8], "big"), 65)


class FakeStream:
    def __init__(self, proc):
        self.proc = proc
        self.sent = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.sent:
            self.sent = True
            return b"Built-in signaling started on http://127.0.0.1:19132\n"
        while self.proc.returncode is None:
            await asyncio.sleep(0.01)
        raise StopAsyncIteration


class FakeProc:
    def __init__(self):
        self.returncode = None
        self.stdout = FakeStream(self)
        self.terminated = False
        self.killed = False
        self.stdin = None

    async def wait(self):
        while self.returncode is None:
            await asyncio.sleep(0.01)
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def kill(self):
        self.killed = True
        self.returncode = -9


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.saved = {}
        for name in ("BEDROCK_DIR", "GEYSER_JAR", "CONFIG", "EXTENSIONS_DIR", "ROUTER_JAR",
                     "ROUTER_DIR", "ROUTES", "BUNDLED_ROUTER_JAR"):
            self.saved[name] = getattr(bedrock, name)
        bedrock.BEDROCK_DIR = self.tmp / "bedrock"
        bedrock.GEYSER_JAR = bedrock.BEDROCK_DIR / "geyser-standalone.jar"
        bedrock.CONFIG = bedrock.BEDROCK_DIR / "config.yml"
        bedrock.EXTENSIONS_DIR = bedrock.BEDROCK_DIR / "extensions"
        bedrock.ROUTER_JAR = bedrock.EXTENSIONS_DIR / "MineNodeRouter.jar"
        bedrock.ROUTER_DIR = bedrock.EXTENSIONS_DIR / "minenode-router"
        bedrock.ROUTES = bedrock.ROUTER_DIR / "routes.properties"
        bedrock.BUNDLED_ROUTER_JAR = self.tmp / "bundle" / "MineNodeRouter.jar"
        bedrock.BUNDLED_ROUTER_JAR.parent.mkdir(parents=True)
        bedrock.BUNDLED_ROUTER_JAR.write_bytes(b"fake-jar")
        bedrock.GEYSER_JAR.parent.mkdir(parents=True)
        bedrock.GEYSER_JAR.write_bytes(b"fake-geyser")

    async def asyncTearDown(self):
        for name, value in self.saved.items():
            setattr(bedrock, name, value)

    async def wait_until(self, predicate, timeout=2.0):
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            if predicate():
                return
            await asyncio.sleep(0.01)
        self.fail("condition was not met in time")

    async def test_apply_starts_and_stops_gateway_and_copies_extension(self):
        inst = FakeInst("a", "Survival", "survival", aliases=["play.example.com"])
        proxy = make_proxy([inst], base_domain="example.com", default_server_id="a")
        proc = FakeProc()

        with patch.object(bedrock, "ensure_java", new=AsyncMock(return_value=(21, Path("java")))), \
                patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)) as spawn:
            await proxy.apply()
            self.assertTrue(proxy.running)
            self.assertTrue(bedrock.CONFIG.exists())
            self.assertTrue(bedrock.ROUTES.exists())
            self.assertTrue(bedrock.ROUTER_JAR.exists())
            self.assertEqual(bedrock.ROUTER_JAR.read_bytes(), b"fake-jar")
            self.assertIn("route.0=play.example.com|a|online|127.0.0.1|25565",
                          bedrock.ROUTES.read_text(encoding="utf-8"))
            spawn.assert_awaited_once()
            args = spawn.await_args.args
            self.assertIn("-jar", args)
            self.assertIn(str(bedrock.GEYSER_JAR), args)
            self.assertIn("--nogui", args)
            self.assertEqual(spawn.await_args.kwargs["cwd"], str(bedrock.BEDROCK_DIR))
            self.assertTrue(spawn.await_args.kwargs["start_new_session"])

            await self.wait_until(lambda: proxy.listening)
            self.assertIn("Built-in signaling started", "\n".join(proxy.log_tail))

            # Already running: a second sync must not spawn another process.
            await proxy.apply()
            spawn.assert_awaited_once()

            inst.cfg.bedrock_enabled = False
            await proxy.apply()
            self.assertFalse(proxy.running)
            self.assertTrue(proc.terminated)
            routes = bedrock.ROUTES.read_text(encoding="utf-8")
            self.assertIn("denied-message=", routes)
            self.assertNotIn("route.", routes)

            await proxy.close()

    async def test_disabled_gateway_does_not_spawn(self):
        inst = FakeInst("a", "Survival", "survival", enabled=False)
        proxy = make_proxy([inst], base_domain="example.com")
        with patch.object(bedrock, "ensure_java", new=AsyncMock()) as ensure, \
                patch("asyncio.create_subprocess_exec", new=AsyncMock()) as spawn:
            await proxy.apply()
            ensure.assert_not_awaited()
            spawn.assert_not_awaited()
            self.assertFalse(proxy.running)
            await proxy.close()

    async def test_start_failure_is_reported_and_backed_off(self):
        inst = FakeInst("a", "Survival", "survival")
        proxy = make_proxy([inst], base_domain="example.com", default_server_id="a")
        with patch.object(bedrock, "ensure_java", new=AsyncMock(side_effect=RuntimeError("no java"))), \
                patch("asyncio.create_subprocess_exec", new=AsyncMock()) as spawn:
            await proxy.apply()
            self.assertIn("no java", proxy.error)
            self.assertGreater(proxy._next_start, 0)
            spawn.assert_not_awaited()
            await proxy.close()

    async def test_auth_mode_change_restarts_only_geyser(self):
        inst = FakeInst("a", "Premium", "premium", online=True)
        proxy = make_proxy([inst], base_domain="example.com", default_server_id="a")
        first, second = FakeProc(), FakeProc()
        spawn = AsyncMock(side_effect=[first, second])
        with patch.object(bedrock, "ensure_java", new=AsyncMock(return_value=(21, Path("java")))), \
                patch("asyncio.create_subprocess_exec", new=spawn):
            await proxy.apply()
            await self.wait_until(lambda: proxy.listening)
            self.assertEqual(proxy._running_auth, "online")

            inst.online_mode = False
            await proxy.apply()
            self.assertTrue(first.terminated)
            self.assertEqual(spawn.await_count, 2)
            await self.wait_until(lambda: proxy.listening)
            self.assertEqual(proxy._running_auth, "offline")
            self.assertIn("  auth-type: offline", bedrock.CONFIG.read_text(encoding="utf-8"))

            # No further restarts when the mode is unchanged.
            await proxy.apply()
            self.assertEqual(spawn.await_count, 2)
            await proxy.close()

    async def test_watch_task_syncs_route_changes(self):
        inst = FakeInst("a", "Survival", "survival")
        inst.cfg.bedrock_enabled = False
        proxy = make_proxy([inst], base_domain="example.com")
        proxy.launch()
        self.assertIsNotNone(proxy._watch_task)
        await proxy.close()
        self.assertFalse(proxy.running)


if __name__ == "__main__":
    unittest.main()
