#!/usr/bin/env bash
# Backup diario de Agent Ventas, $0 y sin dependencias externas.
#
# Orden de preferencia:
#   1. pg_dump contra DATABASE_URL (del .env del repo) → ~/backups/agent-ventas-YYYYMMDD.sql.gz
#   2. sqlite3 .backup sobre data/agent_ventas.db      → ~/backups/agent-ventas-YYYYMMDD.db
#   3. nada alcanzable                                 → mensaje y exit 0
#
# Rota conservando los últimos 7 archivos de cada tipo. Nunca falla
# ruidosamente: cualquier error de backup cae al siguiente método y, en el
# peor caso, sale 0 con un mensaje.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR="${BACKUP_DIR:-${HOME}/backups}"
KEEP="${KEEP:-7}"
STAMP="$(date +%Y%m%d)"

mkdir -p "${BACKUP_DIR}"

log() { printf '%s %s\n' "$(date -Is)" "$*"; }

rotate() {
  # Conserva los KEEP más recientes de cada patrón; el resto, fuera.
  local pattern="$1"
  # shellcheck disable=SC2012
  ls -1t "${BACKUP_DIR}"/${pattern} 2>/dev/null | tail -n "+$((KEEP + 1))" |
    while IFS= read -r old; do
      rm -f -- "${old}"
    done || true
}

# --- 1) Postgres: DATABASE_URL del .env (formato SQLAlchemy) ----------------
db_url=""
if [[ -f "${REPO_DIR}/.env" ]]; then
  db_url="$(grep -m1 '^DATABASE_URL=' "${REPO_DIR}/.env" | cut -d= -f2- || true)"
fi

if [[ -n "${db_url}" ]] && command -v pg_dump >/dev/null 2>&1; then
  # postgresql+psycopg:// → postgresql:// (pg_dump no entiende el dialecto).
  pg_url="${db_url/postgresql+psycopg/postgresql}"
  pg_url="${pg_url/postgresql+psycopg2/postgresql}"
  out="${BACKUP_DIR}/agent-ventas-${STAMP}.sql.gz"
  if pg_dump --no-owner --no-privileges "${pg_url}" 2>>"${BACKUP_DIR}/backup.log" |
    gzip >"${out}.tmp"; then
    mv -f "${out}.tmp" "${out}"
    rotate 'agent-ventas-*.sql.gz'
    log "backup OK (postgres) → ${out}"
    exit 0
  fi
  rm -f "${out}.tmp"
  log "pg_dump no alcanzó la base (${db_url##*@}); pruebo SQLite"
fi

# --- 2) SQLite durable -------------------------------------------------------
sqlite_db="${REPO_DIR}/data/agent_ventas.db"
if [[ -f "${sqlite_db}" ]] && command -v sqlite3 >/dev/null 2>&1; then
  out="${BACKUP_DIR}/agent-ventas-${STAMP}.db"
  if sqlite3 "${sqlite_db}" ".backup '${out}.tmp'"; then
    mv -f "${out}.tmp" "${out}"
    rotate 'agent-ventas-*.db'
    log "backup OK (sqlite) → ${out}"
    exit 0
  fi
  rm -f "${out}.tmp"
fi

# --- 3) Nada que respaldar: sin ruido, exit 0 --------------------------------
log "sin base alcanzable (DATABASE_URL apunta a un cluster caído y no existe data/agent_ventas.db); nada que respaldar"
exit 0
