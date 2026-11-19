"""Plugins/mods the panel installs and updates on its own (web maps, LOD support).

A ``ManagedJar`` describes one project: where its builds come from (Modrinth, optionally a GitHub
release), which build fits which engine, and how its jar is recognised in ``plugins/`` or ``mods/``.
The panel only ever touches jars it recognises; a disabled feature keeps the files as ``.jar.disabled``
so turning it back on does not download anything.
"""
from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Awaitable, Callable, Optional

from . import content, net
from .modpacks import ModpackError

if TYPE_CHECKING:
    from .manager import ServerInstance

_VERSION_TTL = 300.0
_VERSION_RE = re.compile(r"(\d+(?:\.\d+)+)")


def mc_tuple(v: str) -> tuple[int, ...]:
    out = []
    for part in v.split("."):
        if not part.isdigit():
            break
        out.append(int(part))
    return tuple(out)


class ManagedJar:
    """One managed project.

    ``loaders`` maps a MineNode engine to the Modrinth loaders whose builds run on it. ``fallback``
    decides whether a build tagged only for another patch release of the same minor line may be used:
    ``"never"`` (mods tied to an exact version), ``"plugin"`` (plugins only) or ``"26"`` (only on the
    26.x line, whose builds run on every patch release of their minor version). ``extra`` can supply a
    build from somewhere else (e.g. GitHub) when Modrinth has none.
    """

    def __init__(self, key: str, title: str, project_id: str, jar_re: str, loaders: dict[str, list[str]],
                 fallback: str = "plugin",
                 extra: Optional[Callable[["ServerInstance"], Awaitable[Optional[dict]]]] = None,
                 extra_first: Optional[Callable[["ServerInstance"], bool]] = None):
        self.key = key
        self.title = title
        self.project_id = project_id
        self.jar_re = re.compile(jar_re, re.I)
        self.loaders = loaders
        self.fallback = fallback
        self.extra = extra
        self.extra_first = extra_first
        self._cache: dict[tuple[str, str], tuple[float, dict, str]] = {}

    # ---------------------------------------------------------------- support
    def engine_supported(self, inst: "ServerInstance") -> bool:
        return inst.cfg.type in self.loaders

    # ---------------------------------------------------------------- files
    def _base(self, p: Path) -> str:
        return p.name[:-9] if p.name.endswith(".disabled") else p.name

    def jars(self, inst: "ServerInstance") -> list[Path]:
        d = content.content_dir(inst)
        if not d.is_dir():
            return []
        meta = content.load_meta(inst).get("files", {})
        out = []
        for p in d.iterdir():
            base = self._base(p)
            if not base.endswith(".jar"):
                continue
            if self.jar_re.match(base) or meta.get(base, {}).get("project_id") == self.project_id:
                out.append(p)
        return sorted(out, key=lambda p: p.name)

    def installed_info(self, inst: "ServerInstance") -> dict | None:
        jars = self.jars(inst)
        if not jars:
            return None
        meta = content.load_meta(inst).get("files", {})
        enabled = [p for p in jars if p.name.endswith(".jar")]
        p = (enabled or jars)[-1]
        base = self._base(p)
        info = meta.get(base, {})
        managed = info.get("project_id") == self.project_id
        numbers = list(_VERSION_RE.finditer(base))
        return {"file": base, "enabled": p.name.endswith(".jar"), "managed": managed,
                "version_id": info.get("version_id") if managed else None,
                "version_number": (info.get("version_number") if managed else None)
                or (numbers[-1].group(1) if numbers else None)}

    def disable(self, inst: "ServerInstance", managed_only: bool = True) -> bool:
        """Rename the jars to .jar.disabled. A manual install is left alone unless ``managed_only`` is off."""
        info = self.installed_info(inst)
        if not info or (managed_only and not info["managed"]):
            return False
        changed = False
        for p in self.jars(inst):
            if not p.name.endswith(".jar"):
                continue
            target = p.with_name(p.name + ".disabled")
            if target.exists():
                p.unlink(missing_ok=True)
            else:
                p.rename(target)
            changed = True
        return changed

    def enable(self, inst: "ServerInstance") -> None:
        for p in self.jars(inst):
            if p.name.endswith(".jar.disabled"):
                target = p.with_name(p.name[:-9])
                if target.exists():
                    p.unlink(missing_ok=True)
                else:
                    p.rename(target)

    def remove_managed(self, inst: "ServerInstance") -> bool:
        """Delete the jars the panel installed for this project (a manual install is left alone)."""
        meta = content.load_meta(inst)
        removed = False
        for p in self.jars(inst):
            base = self._base(p)
            if meta.get("files", {}).get(base, {}).get("project_id") == self.project_id:
                p.unlink(missing_ok=True)
                meta["files"].pop(base, None)
                removed = True
        if removed:
            content.save_meta(inst, meta)
        return removed

    def _cleanup(self, inst: "ServerInstance", keep: str) -> None:
        for p in self.jars(inst):
            if self._base(p) != keep:
                p.unlink(missing_ok=True)

    # ---------------------------------------------------------------- versions
    def _allow_fallback(self, inst: "ServerInstance") -> bool:
        if self.fallback == "never":
            return False
        if self.fallback == "26":
            return mc_tuple(inst.cfg.mc_version)[:1] >= (26,)
        return content.kind_for(inst.cfg.type) == "plugin"

    async def latest(self, inst: "ServerInstance", refresh: bool = False) -> tuple[dict, str]:
        """The newest build for this engine/version: ``({id, version_number, …}, "ok"|"warn")``."""
        if not self.engine_supported(inst):
            raise ModpackError(f"{self.title} nie działa na silniku {inst.cfg.type}")
        key = (inst.cfg.type, inst.cfg.mc_version)
        hit = self._cache.get(key)
        if hit and not refresh and time.time() - hit[0] < _VERSION_TTL:
            return hit[1], hit[2]
        try:
            found = await self._resolve(inst)
        except ModpackError:
            raise
        except Exception as exc:
            if hit:  # a temporary network failure must not break an installed add-on
                return hit[1], hit[2]
            raise ModpackError(f"Nie udało się sprawdzić wersji {self.title}: {exc}") from exc
        self._cache[key] = (time.time(), found[0], found[1])
        return found

    async def _resolve(self, inst: "ServerInstance") -> tuple[dict, str]:
        if self.extra and self.extra_first and self.extra_first(inst):
            try:
                got = await self.extra(inst)
            except Exception:  # noqa: BLE001 – e.g. GitHub's rate limit: Modrinth below may still have a build
                got = None
            if got:
                return got, got.pop("compat", "ok")
        loaders = self.loaders[inst.cfg.type]
        mc = inst.cfg.mc_version
        versions = await content.modrinth_versions(self.project_id, mc, loaders)
        v = _release(versions)
        if v:
            return {**v, "source": "modrinth"}, "ok"
        if self.extra and not (self.extra_first and self.extra_first(inst)):
            got = await self.extra(inst)
            if got:
                return got, got.pop("compat", "ok")
        if self._allow_fallback(inst):
            every = await content.modrinth_versions(self.project_id, None, loaders)
            same = [x for x in every if any(content.mc_minor(g) == content.mc_minor(mc) for g in x.get("game_versions", []))]
            v = _release(same)
            if v:
                return {**v, "source": "modrinth"}, "warn"
        raise ModpackError(f"{self.title} nie ma wersji dla {inst.cfg.type} {mc}")

    # ---------------------------------------------------------------- install
    def _up_to_date(self, info: dict | None, version: dict) -> bool:
        if not info:
            return False
        if info.get("version_id"):
            return info["version_id"] == version["id"]
        return bool(info.get("version_number")) and info.get("version_number") == version.get("version_number")

    async def install(self, inst: "ServerInstance", version: dict) -> list[dict]:
        if version.get("source") == "modrinth":
            installer = content.Installer(inst, "")
            await installer.modrinth(self.project_id, version_id=version["id"])
            installed = installer.installed
        else:
            installed = await self._install_direct(inst, version)
        info = self.installed_info(inst)
        if info and info.get("file"):
            self._cleanup(inst, info["file"])
        return installed

    async def _install_direct(self, inst: "ServerInstance", version: dict) -> list[dict]:
        d = content.content_dir(inst)
        d.mkdir(parents=True, exist_ok=True)
        dest = d / Path(version["filename"]).name
        inst.panel_log(f"Pobieranie {self.title} {version['version_number']}…")
        await net.download(version["url"], dest, sha256=version.get("sha256"))
        meta = content.load_meta(inst)
        meta["files"][dest.name] = {
            "source": version.get("source", "url"), "project_id": self.project_id, "version_id": version["id"],
            "title": self.title, "version_number": version["version_number"], "installed_at": time.time(),
            "url": version.get("page"),
        }
        content.save_meta(inst, meta)
        return [{"title": self.title, "version": version["version_number"], "compat": "ok", "dependency": False}]

    async def ensure(self, inst: "ServerInstance", refresh: bool = False) -> dict:
        """Install or update the add-on and make sure its jar is enabled."""
        info = self.installed_info(inst)
        try:
            version, compat = await self.latest(inst, refresh=refresh)
        except Exception:
            if not info:
                raise
            await asyncio.to_thread(self.enable, inst)  # offline: keep the installed build
            return {"updated": False, "version": info.get("version_number"), "compat": None, "installed_files": []}
        if self._up_to_date(info, version):
            await asyncio.to_thread(self.enable, inst)
            return {"updated": False, "version": info.get("version_number"), "compat": compat, "installed_files": []}
        files = await self.install(inst, version)
        return {"updated": True, "version": pretty_version(version.get("version_number")), "compat": compat,
                "installed_files": files}

    async def status(self, inst: "ServerInstance", refresh: bool = False) -> dict:
        info = self.installed_info(inst)
        latest, compat, error = None, None, None
        if self.engine_supported(inst):
            try:
                latest, compat = await self.latest(inst, refresh=refresh)
            except Exception as exc:  # noqa: BLE001 – an unavailable API must not break the page
                error = str(exc)
        return {
            "installed": bool(info), "jar_enabled": bool(info and info["enabled"]),
            "version": pretty_version((info or {}).get("version_number")),
            "latest_version": pretty_version((latest or {}).get("version_number")),
            "update_available": bool(info and latest and not self._up_to_date(info, latest)),
            "available": latest is not None, "compat": compat, "error": error,
        }


def pretty_version(v: str | None) -> str | None:
    """'v0.21.1+paper+mc1.21.10' -> '0.21.1' (Modrinth version names that carry the loader/game)."""
    if not v:
        return v
    return re.sub(r"^v(?=\d)", "", v).split("+")[0]


def _release(versions: list[dict]) -> dict | None:
    if not versions:
        return None
    rel = [v for v in versions if v.get("version_type") == "release"]
    return (rel or versions)[0]


# ====================================================================== GitHub releases

_GITHUB_TTL = 3600.0  # the unauthenticated GitHub API allows only 60 requests per hour
_github_cache: dict[str, tuple[float, list]] = {}


async def github_release_asset(repo: str, tag_prefix: str, pick: Callable[[str], bool]) -> Optional[dict]:
    """Newest release (pre-releases included) of ``repo`` whose tag starts with ``tag_prefix`` and has an
    asset accepted by ``pick``."""
    hit = _github_cache.get(repo)
    if hit and time.time() - hit[0] < _GITHUB_TTL:
        releases = hit[1]
    else:
        try:
            releases = await net.get_json(f"https://api.github.com/repos/{repo}/releases",
                                          params={"per_page": 20}, headers={"Accept": "application/vnd.github+json"})
        except Exception:
            if not hit:
                raise
            releases = hit[1]  # keep using the last answer while GitHub refuses
        else:
            _github_cache[repo] = (time.time(), releases)
    for r in releases:
        if r.get("draft") or not str(r.get("tag_name", "")).startswith(tag_prefix):
            continue
        for a in r.get("assets", []):
            if pick(a.get("name", "")):
                digest = str(a.get("digest") or "")
                return {"id": f"{r['tag_name']}/{a['name']}", "version_number": r["tag_name"].removeprefix(tag_prefix),
                        "filename": a["name"], "url": a["browser_download_url"], "page": r.get("html_url"),
                        "sha256": digest.removeprefix("sha256:") if digest.startswith("sha256:") else None,
                        "source": "github", "prerelease": bool(r.get("prerelease"))}
    return None
