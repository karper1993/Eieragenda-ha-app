#!/usr/bin/env bash
set -euo pipefail

SHARE_DIR="/share/eieragenda"
DB_FILE="${SHARE_DIR}/eieragenda.db"
SHARE_APP_DIR="${SHARE_DIR}/app"
BUILTIN_APP_DIR="/app"
OLD_DB="/data/eieragenda.db"

mkdir -p "${SHARE_DIR}"

# Eenmalige migratie: als er nog geen database in /share staat,
# maar er staat nog een oude add-on database in /data, neem die dan mee.
if [ ! -f "${DB_FILE}" ] && [ -f "${OLD_DB}" ]; then
  echo "[Eieragenda] Oude database gevonden in ${OLD_DB}; kopieer naar ${DB_FILE}..."
  cp "${OLD_DB}" "${DB_FILE}"
fi

# Eerste start: zet de app-bestanden ook in /share/eieragenda/app.
# Daardoor kun je later de webapp aanpassen/vervangen via Samba/File Editor
# zonder steeds een nieuwe add-on versie via GitHub te hoeven maken.
if [ ! -f "${SHARE_APP_DIR}/app.py" ]; then
  echo "[Eieragenda] Geen app-bestanden gevonden in ${SHARE_APP_DIR}; kopieer ingebouwde app..."
  mkdir -p "${SHARE_APP_DIR}"
  cp -a "${BUILTIN_APP_DIR}/." "${SHARE_APP_DIR}/"
fi

if [ -f "${SHARE_APP_DIR}/app.py" ]; then
  APP_DIR="${SHARE_APP_DIR}"
else
  echo "[Eieragenda] Waarschuwing: ${SHARE_APP_DIR}/app.py niet gevonden; gebruik ingebouwde app."
  APP_DIR="${BUILTIN_APP_DIR}"
fi

export EIERAGENDA_DB_PATH="${DB_FILE}"

if [ -f "${DB_FILE}" ]; then
  echo "[Eieragenda] Database: ${DB_FILE}"
else
  echo "[Eieragenda] Geen database gevonden; nieuwe database wordt aangemaakt op ${DB_FILE}."
fi

echo "[Eieragenda] App-map: ${APP_DIR}"
echo "[Eieragenda] Start webserver op poort ${EIERAGENDA_PORT:-8099}..."
cd "${APP_DIR}"
exec python3 "${APP_DIR}/app.py"
