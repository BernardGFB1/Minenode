# MineNode

![MineNode dashboard – servers, host resources and proxy](docs/screenshots/dashboard.webp)

A self-hosted panel for running Minecraft servers. It ships as a single Docker container and gives you:

- **7 engines**: Vanilla, Paper, Purpur, Fabric, Quilt, Forge and NeoForge. Versions and builds come live from the official APIs.
- **Automatic Java**: MineNode reads the required Java version from Mojang's metadata and downloads Eclipse Temurin (8/11/17/21/25) through the Adoptium API.
- **A built-in Minecraft reverse proxy**: every server runs behind one port (`25565`), and players land on the right one by subdomain (`survival.mc.example.com`, `creative.mc.example.com`, …).
- **Modpack import** from Modrinth (`.mrpack`) and CurseForge (manifest or official server pack), including uploaded files.
- **One-click mods and plugins**: search Modrinth and CurseForge filtered to the server's engine and Minecraft version. Required dependencies are installed automatically.
- **Compatibility checks**: every file in `mods/` or `plugins/` is identified on Modrinth by SHA-1 and checked against the game version and loader. MineNode suggests updates (including "Update all") and prints warnings about incompatible files in the console on every start.
- **Resource pack hosting on the Minecraft port**: upload a ZIP or get a pack from Modrinth/CurseForge. MineNode validates `pack.mcmeta` (and fixes packs wrapped in a folder), serves the pack over port `25565` from its own subdomain (`packs.mc.example.com`), and writes the URL and SHA-1 into `server.properties` with one click. No extra port is needed.
- **Simple Voice Chat for every server through one UDP port**: install SVC with one click. A shared UDP proxy (`24454`) routes each player to the right server by the player UUID that SVC puts in every packet.
- **Bedrock Edition through one shared Geyser port**: enable Bedrock per server and share a single Bedrock port (`19132/tcp` + `19132/udp`). A bundled Geyser extension routes players by the address they entered to the same server as Java Edition. The Java proxy is not modified.
- **Live console** (WebSocket) with ANSI colours, command history, filtering and WARN/ERROR highlighting.
- **Monitoring**: process CPU and RAM (15-minute charts), online players, host resources and the container memory limit.
- **Lifecycle control**: start, graceful stop, restart and kill. Also autostart, crash auto-restart with backoff, start on join, a daily restart, and **auto-stop when nobody is online**.
- **Point-and-click configuration**: a `server.properties` editor, RAM and JVM flags (Aikar / ZGC) with a live startup command preview.
- **WYSIWYG MOTD editor**: bold, italic, underline, strikethrough, `§k`, the 16 Minecraft colours, special characters and undo/redo. It includes a server-list preview (with animated `§k`) and a raw `§` code mode.
- **Custom offline/starting appearance**: set the MOTD, version label and kick messages for stopped, starting and waking servers, with `{name}`, `{motd}`, `{address}`, `{mc_version}` and `{join_hint}` placeholders.
- **Players**: a whitelist that lists players who tried to join without being on it (add them in one click), OP with a permission level, kick, ban and pardon.
- **File manager**: browse, edit text files, drag-and-drop upload, unzip, and **download every server file as one ZIP** (streamed, no temporary files).
- **Backups anywhere**: consistent backups (`save-off` / `save-all flush`) with scheduling and per-destination retention. You can store them on the panel disk, a **mounted folder** (NAS share, second disk), **S3-compatible storage** (AWS, Backblaze B2, Cloudflare R2, Wasabi, MinIO) or **WebDAV** (Nextcloud, ownCloud, Synology), and on several at once. Download and restore work from any of them.
- **English and Polish UI**: switch the language in the sidebar or on the login screen.

![Creating a server – pick the engine and Minecraft version, Java is chosen automatically](docs/screenshots/new-server.webp)

---

## Quick start

```bash
docker compose up -d
```

Open `http://<host>:8080`. On first visit a setup wizard asks you to create the administrator account, unless you set `MINENODE_ADMIN_USER` / `MINENODE_ADMIN_PASSWORD`.

![MineNode sign-in screen](docs/screenshots/login.webp)

| Port | Purpose |
|------|---------|
| `8080/tcp` | Web panel, API and WebSocket |
| `25565/tcp` | Minecraft: one shared port for every server (proxy), also serves resource packs |
| `24454/udp` | Simple Voice Chat: one shared port for every server |
| `19132/tcp` + `19132/udp` | Bedrock Edition: one shared Geyser port (optional, listens only when Bedrock is enabled) |

All data (servers, worlds, Java runtimes, backups, panel config) lives in the `/data` volume.

### Deploying with Dockge / Portainer

The repository builds a single image, so you can paste the compose file into Dockge. To build straight from GitHub without uploading any files, use a remote build context:

```yaml
services:
  minenode:
    build:
      context: https://github.com/BernardGFB1/Minenode.git#main
    image: minenode:latest
    container_name: minenode
    restart: unless-stopped
    ports:
      - "8080:8080"
      - "25565:25565"
      - "24454:24454/udp"
      - "19132:19132/tcp"
      - "19132:19132/udp"
    volumes:
      - ./data:/data
    environment:
      TZ: Europe/Warsaw
    stop_grace_period: 2m
```

### DNS and subdomains

1. In **Panel settings**, set the *base domain*, e.g. `mc.example.com`.
2. Add a wildcard DNS record: `*.mc.example.com  A  <server IP>` (optionally also `mc.example.com`).
3. Each server gets its own subdomain (e.g. `survival`), so players join `survival.mc.example.com`.

Connections by IP or to an unknown name go to the *default server* (set in the panel). You can also attach any full domain, such as `play.myserver.net`, as an *alias* in the server settings. Bedrock players use the same hostnames on port `19132` (see [Bedrock Edition](#bedrock-edition-geyser)). Like on Java, unknown Bedrock addresses go to the default server (if it has Bedrock enabled), otherwise they are rejected.

### Resource packs

Players download packs straight from the Minecraft port: `http://packs.<base domain>:25565/rp/<server>/<token>/<id>.zip`. The proxy recognises plain HTTP requests on the game port, so no extra port or web server is required. The subdomain (`packs` by default) is configurable in **Panel settings**. If you prefer to serve packs through your own HTTPS reverse proxy, set the *custom resource pack host* there.

### Simple Voice Chat

Open **Server → Settings → Voice chat** and click *Install & enable*. MineNode:

- installs Simple Voice Chat from Modrinth (mod or plugin, matching the engine);
- gives the server a private internal voice port and binds it to `127.0.0.1`;
- sets `voice_host` to `<server address>:24454`, so clients connect to the shared proxy.

The proxy reads the player UUID at the start of every SVC packet and forwards it to the server that player is on. Open UDP `24454` on your firewall.

### Bedrock Edition (Geyser)

MineNode can expose selected servers to Minecraft Bedrock Edition through Geyser Standalone, using one shared Bedrock port (`19132` by default). The Java proxy stays untouched: Geyser connects to it as a normal Java client and forwards the address the player entered, so hostname routing, whitelist, *start on join* and voice chat keep working exactly as for Java players.

MineNode automatically checks the Java version supported by the current Geyser build and keeps Geyser updated from the official Downloads API. Older Paper/Purpur servers receive a managed ViaVersion plugin; Fabric servers receive ViaFabric and Fabric API. The gateway is restarted by itself after a Geyser update, while Minecraft servers are not restarted. Vanilla, Forge, NeoForge and Quilt servers are blocked when they need a translator that their loader cannot provide.

### Portable server packages

In **Server → Settings**, use **Export .minenodepack** to create a portable package containing the server configuration, world, installed engine, plugins/mods, resource packs, `.minenode` metadata and per-server player data. The server must be stopped before export so the world is consistent. On the dashboard, **Import .minenodepack** creates a new server with a new ID and automatically allocated internal/voice ports; the original server is not overwritten. Packages contain a versioned manifest and can be imported by later MineNode releases as long as the package schema is supported. Hostnames and aliases are checked for conflicts during import.

1. Open **Server → Settings → Bedrock Edition (Geyser)** and enable *Share via Bedrock* for the servers you want. Geyser is enabled independently of the Java proxy and does not affect it.
2. Open **TCP and UDP** port `19132` on your firewall and router. NetherNet uses TCP signalling and UDP gameplay on the same port.
3. Players add a server in Bedrock with the same address Java players use, e.g. `survival.mc.example.com`, port `19132`. The address must resolve on the player's device; Bedrock has no SRV records.
4. Set **Public address** in the panel settings (or *Address advertised to Bedrock players*). After finding the server, Bedrock opens the game connection to the address Geyser advertises. Without one, Geyser guesses the public address through STUN, and behind Docker or a home router NAT usually picks a random port that is not forwarded: players see the server but cannot join. If no public address is set, MineNode advertises a server hostname under the base domain when it resolves.

Port `19132` is shared by every server, so there is no per-server Bedrock port to open. Disabled or incompatible servers keep their hostnames reserved: connecting to their address is rejected instead of reaching that server or landing on another one. Enabling the first server downloads Geyser and Java 21 automatically; the gateway starts when the first server enables Bedrock and stops when none do.

**Authentication:** Geyser has one global auth mode. If **any** enabled Bedrock server runs in online mode, MineNode sets `auth-type: online`, and every Bedrock player must have a Java Edition account linked to their Xbox account. When all enabled Bedrock servers are offline-mode, MineNode switches to `offline`, so Bedrock players do not need Java Edition. The mode is re-evaluated automatically, and only Geyser restarts when it changes — the Java proxy and servers never restart. Letting Bedrock players join an online-mode server without Java Edition requires Floodgate, which MineNode does not install yet.

For `auth-type: online`, Geyser asks Microsoft only once per saved Bedrock username. Set `MINENODE_BEDROCK_SAVED_LOGINS` to the Bedrock gamertag(s), separated by commas, for example `PlayerOne,PlayerTwo`, then recreate the container. Geyser stores refresh tokens in `/data/bedrock/saved-refresh-tokens.json`; it stores tokens, not passwords. Do not expose that file or include it in public backups.

### HTTPS for the panel

Put the panel behind an HTTP reverse proxy (Caddy, Traefik, nginx) with TLS and set `MINENODE_SECURE_COOKIES=true`. For example, with Caddy:

```
panel.example.com {
    reverse_proxy minenode:8080
}
```

WebSockets work out of the box. Expose the Minecraft (`25565/tcp`), voice (`24454/udp`) and Bedrock (`19132/tcp` + `19132/udp`) ports directly.

### Backup destinations

Add destinations in **Panel settings → Backup destinations**, then pick them in each server's **Backups** tab (both for manual and scheduled backups). Every destination has a *Test connection* button that writes, reads and deletes a small file.

| Type | What to enter |
|------|---------------|
| Folder / NAS | A path **inside the container**. Mount it first, e.g. `- /mnt/nas/minecraft-backups:/backups/nas`, then use `/backups/nas` |
| S3 | Bucket, prefix, access/secret key; endpoint + region for non-AWS providers (presets included). Enable *path-style* for MinIO |
| WebDAV | Folder URL (e.g. `https://cloud.example.com/remote.php/dav/files/<user>/Backups`), username and password (use an app password in Nextcloud) |

Backups are stored as `<server-id>/<backup-id>.zip` plus a `.json` sidecar with metadata, so a destination can be browsed by hand. Large uploads to S3 use multipart transfers. Deleting a server removes only the copies on the panel disk, and backups in external destinations are kept.

### CurseForge

CurseForge needs your own free API key from [console.curseforge.com](https://console.curseforge.com/). Paste it into **Panel settings** or set `CURSEFORGE_API_KEY`. Modrinth works without a key.

Some authors block API downloads of their files. MineNode lists those files in the console so you can upload them to `mods/` with the file manager.

---

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MINENODE_PORT` | `8080` | Panel port |
| `MINENODE_PROXY_PORT` | `25565` | Public Minecraft port (proxy) |
| `MINENODE_VOICE_PORT` | `24454` | Public Simple Voice Chat UDP port |
| `MINENODE_BEDROCK_PORT` | `19132` | Public Bedrock port (Geyser, TCP + UDP) |
| `MINENODE_BEDROCK_SAVED_LOGINS` | – | Comma-separated Bedrock usernames whose Microsoft login token Geyser should save and refresh |
| `MINENODE_INTERNAL_PORT_START` | `25600` | First internal game port (assigned automatically) |
| `MINENODE_INTERNAL_VOICE_PORT_START` | `24500` | First internal voice port (assigned automatically) |
| `MINENODE_DATA` | `/data` | Data directory |
| `MINENODE_ADMIN_USER` / `MINENODE_ADMIN_PASSWORD` | – | Account created on first start (instead of the wizard) |
| `MINENODE_SECURE_COOKIES` | `false` | Send session cookies over HTTPS only |
| `CURSEFORGE_API_KEY` | – | CurseForge API key |
| `PUID` / `PGID` | `1000` | User the panel and servers run as |
| `TZ` | `Europe/Warsaw` | Time zone (daily restarts, backups) |

---

## Architecture

```
                 ┌──────────────────────────── minenode container ───────────────────────────┐
 browser ───────▶│ :8080  FastAPI ── REST /api/* ── WebSocket /api/servers/{id}/ws            │
                 │          │           Vue 3 SPA (static files)                              │
                 │          ▼                                                                 │
                 │      Manager ── ServerInstance × N ── java -Xmx… -jar …  (:25600, :25601…)  │
                 │          │            ▲  stdin/stdout (console, commands, log parsing)      │
 Minecraft ─────▶│ :25565 MinecraftProxy ┘  handshake → hostname → internal port               │
                 │          └─ HTTP GET /rp/… → resource pack files                           │
 voice (UDP) ───▶│ :24454 VoiceProxy ── player UUID → server's internal SVC port (:24500…)    │
 Bedrock ───────▶│ :19132 Geyser + MineNodeRouter ── hostname ──▶ :25565 proxy                │
                 │   Java (Temurin, auto)   Providers (Mojang/Paper/Purpur/Fabric/…)          │
                 │   Backups (zip)          Modpacks & content (Modrinth / CurseForge)        │
                 └──────────────────────────────────── /data ─────────────────────────────────┘
```

**Backend** (`backend/app`, Python 3.12, FastAPI, asyncio):

| Module | Role |
|--------|------|
| `main.py` | App and lifecycle (graceful server shutdown on `docker stop`), CSRF, SPA serving |
| `api.py` | REST + WebSocket endpoints |
| `manager.py` | Server processes, console, metrics (psutil), log parsing (players, whitelist rejections), scheduler, idle shutdown |
| `proxy.py` | Minecraft-aware TCP proxy: parses Handshake/Login Start, routes by host, answers for offline servers, optional PROXY protocol v2, serves resource packs over HTTP on the same port |
| `voice.py` | UDP proxy for Simple Voice Chat (routing by player UUID) and SVC config management |
| `bedrock.py` | Shared Geyser Bedrock gateway: downloads Geyser, generates `config.yml` and hostname routes, manages the process and global auth mode. `backend/bedrock_ext/` is the bundled MineNodeRouter extension that resolves each Bedrock hostname (compiled JAR is committed) |
| `providers.py` | Version discovery and engine installation (incl. Forge/NeoForge/Quilt installers) |
| `java.py` | Java selection and download from Adoptium |
| `modpacks.py` | Modrinth `.mrpack`, CurseForge manifests and server packs, file import |
| `content.py` | Mods & plugins: search, install with dependencies, inventory, compatibility checks, updates |
| `resourcepacks.py` | Resource pack library, hosting, `server.properties` activation |
| `backups.py` | Consistent ZIP backups across destinations, per-destination retention, restore with rollback on failure |
| `storage.py` | Backup storage backends: panel disk, mounted folder, S3 (boto3), WebDAV |
| `players.py` | whitelist/ops/bans JSON, UUIDs from Mojang or offline mode |
| `files.py` | Safe paths, streamed ZIP downloads |
| `auth.py` | scrypt passwords, HMAC-signed sessions (httpOnly, SameSite=Strict), login rate limiting |

**Frontend** (`frontend/`, Vue 3 + Vite, no UI framework): a custom design system with light and dark themes, Lucide icons, and Inter / JetBrains Mono / Pixelify Sans fonts bundled locally (no external CDNs).

**Translations**: Polish source strings are the keys (`$t('…')`) and `frontend/src/locales/en.js` holds the English dictionary. Messages coming from the backend are matched against templates in the same file. A missing translation falls back to Polish, so new features never break the UI.

### `/data` layout

```
/data
├── servers/<id>/          # server files (world, mods/, plugins/, server.properties…)
│   └── .minenode/         # panel metadata: content.json (mods/plugins), resourcepacks/
├── backups/<id>/*.zip     # backups + .json metadata
├── java/temurin-<N>/      # downloaded Java runtimes
├── panel/                 # servers.json, settings.json, users.json, secret.key, players/
├── bedrock/               # Geyser jar, config.yml, logs, extensions/MineNodeRouter
└── tmp/                   # temporary install files
```

---

## Local development

```bash
# backend
cd backend && python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
MINENODE_DATA=./data MINENODE_PROXY_PORT=25599 MINENODE_VOICE_PORT=24499 MINENODE_BEDROCK_PORT=19199 uvicorn app.main:app --reload --port 8080

# frontend (proxies /api to :8080, hot reload)
cd frontend && npm install && npm run dev
```

API docs (OpenAPI): `http://localhost:8080/api/docs`.

---

## Security

- The panel runs servers as an unprivileged user (`PUID`/`PGID`), never as root.
- State-changing requests require an `X-Requested-With` header (CSRF protection), and the WebSocket checks `Origin`.
- Paths in the file manager, modpack archives and backups are checked against directory traversal (zip-slip).
- Resource pack URLs contain an unguessable token, and the voice ports of individual servers are bound to `127.0.0.1`.
- Servers start with `-Dlog4j2.formatMsgNoLookups=true` (Log4Shell protection for older versions).
- If you set `MINENODE_ADMIN_PASSWORD` in a public compose file, change the password in the panel after the first login.

## Limitations

- Bedrock Edition is supported through Geyser on one shared port (`19132/tcp` + `19132/udp`), enabled per server. Geyser's global auth mode means that when any enabled server is online-mode, Bedrock players need a linked Java Edition account; Floodgate is not included.
- The pre-1.7 legacy server list ping is not routed.
- Behind the proxy, servers see players as `127.0.0.1`. To pass real IPs, enable *PROXY protocol* in the server settings and on the server side (e.g. Paper: `proxies.proxy-protocol: true`).
