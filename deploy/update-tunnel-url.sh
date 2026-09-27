#!/usr/bin/env bash
# Propaga la URL nueva del quick tunnel al Worker de la landing (saleamind1).
#
# Idempotente y "git-free": solo hace `wrangler deploy` si la URL detectada
# en el log del tunnel cambió respecto a la última guardada en
# deploy/.last_tunnel_url. Si no cambió, sale 0 sin hacer nada.
# flock evita que dos ejecuciones del timer se solapen.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEPLOY_DIR="${REPO_DIR}/deploy"
STATE_FILE="${DEPLOY_DIR}/.last_tunnel_url"
TUNNEL_LOG="${TUNNEL_LOG:-${HOME}/agent-ventas-tunnel.log}"
WATCH_LOG="${WATCH_LOG:-${HOME}/agent-ventas-tunnel-watch.log}"
LANDING_DIR="${LANDING_DIR:-/home/diego/Factory/web/saleamind1}"
LOCK_FILE="${TMPDIR:-/tmp}/agent-ventas-tunnel-watch.lock"

# npx/node viven fuera del PATH por defecto de las unidades de usuario.
export PATH="${HOME}/.local/share/node-v24/bin:${HOME}/.local/bin:/usr/local/bin:/usr/bin:/bin"

log() { printf '%s %s\n' "$(date -Is)" "$*" >>"${WATCH_LOG}"; }

# Un solo watcher a la vez: si el anterior sigue corriendo, salir limpio.
exec 9>"${LOCK_FILE}"
if ! flock -n 9; then
  exit 0
fi

# Sin log del tunnel todavía (nunca arrancó) → nada que propagar.
[[ -s "${TUNNEL_LOG}" ]] || exit 0

# La última URL impresa es la vigente (el log se abre en append y cada
# reinicio de cloudflared imprime una URL nueva al final).
current_url="$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "${TUNNEL_LOG}" | tail -n 1 || true)"
[[ -n "${current_url}" ]] || exit 0

last_url=""
[[ -r "${STATE_FILE}" ]] && last_url="$(cat "${STATE_FILE}")"

if [[ "${current_url}" == "${last_url}" ]]; then
  # Sin cambios: exit 0 sin tocar la landing.
  exit 0
fi

log "URL nueva ${current_url} (anterior: ${last_url:-ninguna}); redeploy de la landing"

# El output va al log del watcher: si wrangler falla, el state no se
# actualiza y el siguiente tick reintenta.
if (
  cd "${LANDING_DIR}"
  npx wrangler deploy --var "ANA_API_URL:${current_url}"
) >>"${WATCH_LOG}" 2>&1; then
  printf '%s\n' "${current_url}" >"${STATE_FILE}"
  log "wrangler deploy OK → ${current_url}"
else
  log "wrangler deploy FALLÓ; se reintenta en el próximo tick"
  exit 1
fi
