#!/usr/bin/env bash
set -euo pipefail

DB_DIR="/share/eieragenda"
DB_FILE="${DB_DIR}/eieragenda.db"
OLD_DB="/data/eieragenda.db"

mkdir -p "${DB_DIR}"

# Eenmalige migratie: als er nog geen database in /share staat,
# maar er staat nog een oude add-on database in /data, neem die dan mee.
if [ ! -f "${DB_FILE}" ] && [ -f "${OLD_DB}" ]; then
  echo "[Eieragenda] Oude database gevonden in ${OLD_DB}; kopieer naar ${DB_FILE}..."
  cp "${OLD_DB}" "${DB_FILE}"
fi

export EIERAGENDA_DB_PATH="${DB_FILE}"

if [ -f "${DB_FILE}" ]; then
  echo "[Eieragenda] Database: ${DB_FILE}"
else
  echo "[Eieragenda] Geen database gevonden; nieuwe database wordt aangemaakt op ${DB_FILE}."
fi

echo "[Eieragenda] Start webserver op poort ${EIERAGENDA_PORT:-8099}..."
exec python3 /app/app.py
