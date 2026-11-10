#!/bin/sh
# Runs the panel as an unprivileged user (PUID/PGID), fixing /data ownership on first start.
set -e
PUID="${PUID:-1000}"
PGID="${PGID:-1000}"

if [ "$(id -u)" = "0" ]; then
  if [ "$(id -g minenode)" != "$PGID" ]; then groupmod -o -g "$PGID" minenode; fi
  if [ "$(id -u minenode)" != "$PUID" ]; then usermod -o -u "$PUID" minenode; fi
  mkdir -p /data
  if [ "$(stat -c %u /data)" != "$PUID" ] || [ "$(stat -c %g /data)" != "$PGID" ]; then
    echo "[minenode] Ustawianie właściciela /data na $PUID:$PGID…"
    chown -R "$PUID:$PGID" /data
  fi
  export HOME=/home/minenode
  exec setpriv --reuid="$PUID" --regid="$PGID" --init-groups "$@"
fi
exec "$@"
