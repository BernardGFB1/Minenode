"""Persistent data models."""
from __future__ import annotations

import re
import time
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

SERVER_TYPES = ("vanilla", "paper", "purpur", "fabric", "quilt", "forge", "neoforge", "custom")
ServerType = Literal["vanilla", "paper", "purpur", "fabric", "quilt", "forge", "neoforge", "custom"]
FlagsPreset = Literal["aikar", "zgc", "none"]

SUBDOMAIN_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
HOSTNAME_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*$")


class JavaConfig(BaseModel):
    version: Optional[int] = None  # None -> automatic (based on Minecraft version)
    min_ram_mb: int = Field(default=1024, ge=256, le=1024 * 512)
    max_ram_mb: int = Field(default=4096, ge=512, le=1024 * 512)
    flags_preset: FlagsPreset = "aikar"
    extra_jvm_args: str = ""
    extra_server_args: str = ""


class ChunkySettings(BaseModel):
    """World pre-generation with Chunky (Add-ons tab)."""
    world: Literal["overworld", "the_nether", "the_end"] = "overworld"
    shape: Literal["square", "circle", "rectangle", "ellipse", "diamond", "pentagon", "hexagon", "star",
                   "triangle"] = "square"
    pattern: Literal["concentric", "loop", "spiral"] = "concentric"
    center_x: int = Field(default=0, ge=-30_000_000, le=30_000_000)
    center_z: int = Field(default=0, ge=-30_000_000, le=30_000_000)
    radius: int = Field(default=2000, ge=16, le=1_000_000)  # blocks from the center
    only_when_empty: bool = True  # pause while players are online
    multicore: bool = False  # Paper: worker-threads = cores - 1; Fabric/Quilt: C2ME


WebMapProvider = Literal["squaremap", "dynmap", "bluemap"]


class WebMapSettings(BaseModel):
    """The live web map on the public server page (Add-ons → Services)."""
    enabled: bool = False
    provider: WebMapProvider = "squaremap"
    show_players: bool = True  # player positions on the map
    render_3d: bool = False  # Dynmap/BlueMap: False renders only the light flat (2D) view
    port: Optional[int] = None  # loopback web server of Dynmap/BlueMap, proxied by the panel


class LodSettings(BaseModel):
    """Server-side LOD (distant terrain) for Distant Horizons and Voxy clients."""
    distant_horizons: bool = False
    voxy: bool = False
    distance: int = Field(default=128, ge=32, le=512)  # radius in chunks sent to players
    generate: bool = False  # create missing chunks for LODs (costly) – otherwise existing terrain only


class BackupSchedule(BaseModel):
    enabled: bool = False
    interval_hours: float = Field(default=24, gt=0, le=24 * 30)
    keep: int = Field(default=7, ge=1, le=500)
    destinations: list[str] = ["local"]  # where scheduled (and default manual) backups go


class BackupDestination(BaseModel):
    """A place to store backups: the panel disk, a mounted folder (NAS), S3-compatible storage or WebDAV."""
    id: str
    name: str = Field(min_length=1, max_length=64)
    type: Literal["local", "folder", "s3", "webdav"]
    path: str = ""          # folder: absolute path inside the container (e.g. a NAS mount)
    endpoint: str = ""      # s3: empty = AWS, otherwise e.g. https://s3.eu-central-003.backblazeb2.com
    region: str = ""
    bucket: str = ""
    prefix: str = "minenode"
    access_key: str = ""
    secret_key: str = ""
    path_style: bool = False  # s3: needed by MinIO and many self-hosted servers
    url: str = ""           # webdav: folder URL, e.g. https://cloud.example.com/remote.php/dav/files/me/Backups
    username: str = ""
    password: str = ""


class ModpackInfo(BaseModel):
    source: Literal["modrinth", "curseforge", "file"]
    project_id: Optional[str] = None
    version_id: Optional[str] = None
    name: str = ""
    version_name: str = ""
    icon_url: Optional[str] = None


class LaunchSpec(BaseModel):
    kind: Literal["jar", "argsfile"]
    target: str  # jar filename or args file relative path


class ServerConfig(BaseModel):
    id: str
    name: str
    type: ServerType
    mc_version: str
    loader_version: Optional[str] = None
    subdomain: str
    aliases: list[str] = []
    internal_port: int
    java: JavaConfig = JavaConfig()
    auto_start: bool = False
    auto_restart: bool = True
    start_on_join: bool = False
    proxy_protocol: bool = False
    restart_daily_at: Optional[str] = None  # "HH:MM"
    idle_shutdown_minutes: int = Field(default=0, ge=0, le=24 * 60)  # 0 = disabled
    domain: Optional[str] = None  # main domain shown in this server's address (None = the panel's first domain)
    voice_enabled: bool = False  # Simple Voice Chat routed through the shared UDP proxy
    voice_port: Optional[int] = None  # internal SVC port (allocated automatically)
    bedrock_enabled: bool = False  # routed through the shared Geyser Bedrock gateway
    auth_enabled: bool = True  # routed through the hidden PicoLimbo lobby when offline-mode
    webmap: WebMapSettings = WebMapSettings()  # live web map served by the panel under /map/<id>/
    lod: LodSettings = LodSettings()
    chunky: ChunkySettings = ChunkySettings()
    backup: BackupSchedule = BackupSchedule()
    modpack: Optional[ModpackInfo] = None
    launch: Optional[LaunchSpec] = None
    required_java: Optional[int] = None
    installed: bool = False
    created_at: float = Field(default_factory=time.time)
    last_backup_at: Optional[float] = None

    @model_validator(mode="before")
    @classmethod
    def _migrate_squaremap(cls, data):
        # Before the map choice existed, the map was squaremap with its own switch.
        if isinstance(data, dict) and "webmap" not in data and data.get("squaremap_enabled"):
            data = {**data, "webmap": {"enabled": True, "provider": "squaremap"}}
        return data

    @property
    def squaremap_enabled(self) -> bool:
        return self.webmap.enabled and self.webmap.provider == "squaremap"

    @field_validator("subdomain")
    @classmethod
    def _check_sub(cls, v: str) -> str:
        v = v.strip().lower()
        if not SUBDOMAIN_RE.match(v):
            raise ValueError("Subdomena może zawierać tylko a-z, 0-9 i '-'")
        return v

    @field_validator("aliases")
    @classmethod
    def _check_aliases(cls, v: list[str]) -> list[str]:
        out = []
        for a in v:
            a = a.strip().lower().rstrip(".")
            if not a:
                continue
            if not HOSTNAME_RE.match(a):
                raise ValueError(f"Niepoprawna nazwa hosta: {a}")
            out.append(a)
        return out

    @field_validator("restart_daily_at")
    @classmethod
    def _check_time(cls, v: Optional[str]) -> Optional[str]:
        if v in (None, ""):
            return None
        if not re.match(r"^([01]\d|2[0-3]):[0-5]\d$", v):
            raise ValueError("Format godziny: HH:MM")
        return v


class PanelSettings(BaseModel):
    base_domain: str = ""  # the first (default) main domain
    extra_domains: list[str] = []  # more main domains: every server answers as <subdomain>.<each domain>
    default_server_id: Optional[str] = None
    curseforge_api_key: str = ""
    public_address: str = ""  # shown to users when no base domain is configured
    public_panel_url: str = ""  # optional override for resource pack URLs (otherwise served on the Minecraft port)
    pack_subdomain: str = "packs"  # resource packs: http://<pack_subdomain>.<base_domain>:<proxy port>/rp/…
    # Bedrock (Geyser NetherNet): addresses sent to players for the game connection, comma-separated, optional
    # ":port". Empty = public_address / a server hostname, else STUN (unreliable behind NAT that remaps ports).
    bedrock_advertise: str = ""
    # Which Geyser build the Bedrock gateway runs: "stable" (newest promoted release) or "preview" – a test
    # build from a Geyser pull request (download.geysermc.org project "geyserpreview", version "pr.<number>").
    bedrock_geyser_channel: Literal["stable", "preview"] = "stable"
    bedrock_geyser_preview: str = "pr.6873"
    # Geyser's integrated resource pack (sent to every Bedrock player). Off only for diagnosing slow joins.
    bedrock_integrated_pack: bool = True
    # What the proxy shows for servers that are not running. Placeholders:
    # {name} {motd} {address} {mc_version} {join_hint}
    motd_offline: str = "§f{name}\n§7● Serwer jest wyłączony{join_hint}"
    motd_starting: str = "§f{name}\n§e● Serwer się uruchamia…"
    version_offline: str = "§c● Offline"
    version_starting: str = "§e● Uruchamianie"
    kick_offline: str = "{name} jest wyłączony."
    kick_starting: str = "{name} uruchamia się – spróbuj ponownie za chwilę."
    backup_destinations: list[BackupDestination] = []
    kick_waking: str = "{name} właśnie się uruchamia!\nDołącz ponownie za około minutę."

    def domains(self) -> list[str]:
        """Every main domain, the default one first (normalised, without duplicates)."""
        out: list[str] = []
        for d in [self.base_domain, *self.extra_domains]:
            d = (d or "").strip().lower().strip(".")
            if d and d not in out:
                out.append(d)
        return out


class User(BaseModel):
    username: str
    password_hash: str
    created_at: float = Field(default_factory=time.time)
