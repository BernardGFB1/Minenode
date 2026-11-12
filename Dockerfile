# syntax=docker/dockerfile:1.7
# ---------------------------------------------------------------- frontend build
FROM node:22-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---------------------------------------------------------------- runtime
FROM python:3.12-slim-bookworm
LABEL org.opencontainers.image.title="MineNode" \
      org.opencontainers.image.description="Minecraft server management panel with built-in reverse proxy"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    MINENODE_DATA=/data \
    MINENODE_STATIC=/app/static \
    MINENODE_PORT=8080 \
    MINENODE_PROXY_PORT=25565 \
    MINENODE_VOICE_PORT=24454 \
    MINENODE_BEDROCK_PORT=19132 \
    TZ=Europe/Warsaw

# tini: proper PID 1 / signal forwarding; fontconfig+freetype: needed by some mods (maps, image rendering)
RUN apt-get update \
 && apt-get install -y --no-install-recommends tini ca-certificates tzdata fontconfig libfreetype6 procps \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd -g 1000 minenode && useradd -u 1000 -g 1000 -m -d /home/minenode minenode

WORKDIR /app
COPY backend/requirements.txt ./
RUN pip install -r requirements.txt
COPY backend/app ./app
COPY backend/auth_plugin/MineNodeAuth.jar ./auth_plugin/MineNodeAuth.jar
COPY backend/bedrock_ext/MineNodeRouter.jar ./bedrock_ext/MineNodeRouter.jar
COPY --from=web /web/dist ./static
COPY docker/entrypoint.sh /entrypoint.sh
# strip Windows line endings (a CRLF checkout breaks the shebang: "exec /entrypoint.sh failed")
RUN sed -i 's/\r$//' /entrypoint.sh && chmod +x /entrypoint.sh && mkdir -p /data && chown minenode:minenode /data

VOLUME ["/data"]
EXPOSE 8080 25565 24454/udp 19132/tcp 19132/udp

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,os;urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"MINENODE_PORT\",\"8080\")}/api/health',timeout=4)" || exit 1

ENTRYPOINT ["/usr/bin/tini", "--", "/entrypoint.sh"]
CMD ["sh", "-c", "exec python -m uvicorn app.main:app --host 0.0.0.0 --port ${MINENODE_PORT} --proxy-headers --forwarded-allow-ips='*' --no-server-header"]
