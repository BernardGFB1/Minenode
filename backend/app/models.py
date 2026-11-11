"""Persistent data models."""
from __future__ import annotations

import re
import time
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

SERVER_TYPES = ("vanilla", "paper", "purpur", "fabric", "quilt", "forge", "neoforge")
ServerType = Literal["vanilla", "paper", "purpur", "fabric", "quilt", "forge", "neoforge"]
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
    voice_enabled: bool = False  # Simple Voice Chat routed through the shared UDP proxy
    voice_port: Optional[int] = None  # internal SVC port (allocated automatically)
    bedrock_enabled: bool = False  # routed through the shared Geyser Bedrock gateway
    backup: BackupSchedule = BackupSchedule()
    modpack: Optional[ModpackInfo] = None
    launch: Optional[LaunchSpec] = None
    required_java: Optional[int] = None
    installed: bool = False
    created_at: float = Field(default_factory=time.time)
    last_backup_at: Optional[float] = None

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
    base_domain: str = ""
    default_server_id: Optional[str] = None
    curseforge_api_key: str = ""
    public_address: str = ""  # shown to users when no base domain is configured
    public_panel_url: str = ""  # optional override for resource pack URLs (otherwise served on the Minecraft port)
    pack_subdomain: str = "packs"  # resource packs: http://<pack_subdomain>.<base_domain>:<proxy port>/rp/…
    # Bedrock (Geyser NetherNet): addresses sent to players for the game connection, comma-separated, optional
    # ":port". Empty = public_address / a server hostname, else STUN (unreliable behind NAT that remaps ports).
    bedrock_advertise: str = ""
    # What the proxy shows for servers that are not running. Placeholders:
    # {name} {motd} {address} {mc_version} {join_hint}
    motd_offline: str = "§f{name}\n§7● Serwer jest wyłączony{join_hint}"
    motd_starting: str = "§f{name}\n§e● Serwer się uruchamia…"
    version_offline: str = "§c● Offline"
    version_starting: str = "§e● Uruchamianie"
    kick_offline: str = "§c{name} jest wyłączony."
    kick_starting: str = "§e{name} uruchamia się – spróbuj ponownie za chwilę."
    backup_destinations: list[BackupDestination] = []
    kick_waking: str = "§a{name} właśnie się uruchamia!\n§7Dołącz ponownie za około minutę."


class User(BaseModel):
    username: str
    password_hash: str
    created_at: float = Field(default_factory=time.time)
