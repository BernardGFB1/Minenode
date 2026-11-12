"""Runtime configuration (environment driven) and data directory layout."""
from __future__ import annotations

import os
from pathlib import Path

VERSION = "1.0.0"
APP_NAME = "MineNode"


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


DATA_DIR = Path(os.environ.get("MINENODE_DATA", "/data")).resolve()
SERVERS_DIR = DATA_DIR / "servers"
BACKUPS_DIR = DATA_DIR / "backups"
JAVA_DIR = DATA_DIR / "java"
CACHE_DIR = DATA_DIR / "cache"
PANEL_DIR = DATA_DIR / "panel"
TMP_DIR = DATA_DIR / "tmp"
AUTH_LOBBY_DIR = DATA_DIR / "auth-lobby"

PANEL_HOST = os.environ.get("MINENODE_HOST", "0.0.0.0")
PANEL_PORT = _env_int("MINENODE_PORT", 8080)
PROXY_HOST = os.environ.get("MINENODE_PROXY_HOST", "0.0.0.0")
PROXY_PORT = _env_int("MINENODE_PROXY_PORT", 25565)
INTERNAL_PORT_START = _env_int("MINENODE_INTERNAL_PORT_START", 25600)
VOICE_HOST = os.environ.get("MINENODE_VOICE_HOST", PROXY_HOST)
VOICE_PORT = _env_int("MINENODE_VOICE_PORT", 24454)  # shared public UDP port for Simple Voice Chat
INTERNAL_VOICE_PORT_START = _env_int("MINENODE_INTERNAL_VOICE_PORT_START", 24500)
AUTH_LOBBY_PORT = _env_int("MINENODE_AUTH_LOBBY_PORT", 25999)
BEDROCK_HOST = os.environ.get("MINENODE_BEDROCK_HOST", PROXY_HOST)
BEDROCK_PORT = _env_int("MINENODE_BEDROCK_PORT", 19132)  # shared public port (TCP signaling + UDP gameplay) for Geyser
BEDROCK_SAVED_LOGINS = tuple(
    name.strip() for name in os.environ.get("MINENODE_BEDROCK_SAVED_LOGINS", "").split(",") if name.strip()
)

# Optional bootstrap admin account (only used when no user exists yet).
BOOTSTRAP_USER = os.environ.get("MINENODE_ADMIN_USER")
BOOTSTRAP_PASSWORD = os.environ.get("MINENODE_ADMIN_PASSWORD")
CURSEFORGE_API_KEY_ENV = os.environ.get("CURSEFORGE_API_KEY", "")
SECURE_COOKIES = os.environ.get("MINENODE_SECURE_COOKIES", "false").lower() in ("1", "true", "yes")

STATIC_DIR = Path(os.environ.get("MINENODE_STATIC", Path(__file__).resolve().parent.parent / "static"))

USER_AGENT = f"minenode-panel/{VERSION} (self-hosted Minecraft server manager)"


def ensure_dirs() -> None:
    for d in (DATA_DIR, SERVERS_DIR, BACKUPS_DIR, JAVA_DIR, CACHE_DIR, PANEL_DIR, TMP_DIR, AUTH_LOBBY_DIR):
        d.mkdir(parents=True, exist_ok=True)
