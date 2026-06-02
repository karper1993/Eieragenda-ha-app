#!/usr/bin/env bash
set -euo pipefail

mkdir -p /data

RESTORE_FROM_SHARE="false"
if [ -f /data/options.json ]; then
  RESTORE_FROM_SHARE="$(jq -r '.restore_from_share // false' /data/options.json)"
fi

if [ "${RESTORE_FROM_SHARE}" = "true" ] && [ ! -f /data/eieragenda.db ] && [ -f /share/eieragenda.db ]; then
  echo "[Eieragenda] Bestaande database gevonden in /share/eieragenda.db. Kopieer naar /data/eieragenda.db..."
  cp /share/eieragenda.db /data/eieragenda.db
fi

if [ -f /data/eieragenda.db ]; then
  echo "[Eieragenda] Database: /data/eieragenda.db"
else
  echo "[Eieragenda] Geen database gevonden; nieuwe database wordt aangemaakt."
fi

echo "[Eieragenda] Start webserver op poort ${EIERAGENDA_PORT:-8099}..."
exec python3 /app/app.py
