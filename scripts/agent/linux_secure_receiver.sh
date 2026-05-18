#!/usr/bin/env bash
set -euo pipefail

# Linux secure receiver for hub-trigger mode (no SSH, no Python required).
# Requires: socat, openssl, jq, curl.

LISTEN_HOST="${LISTEN_HOST:-0.0.0.0}"
LISTEN_PORT="${LISTEN_PORT:-9189}"
ROUTE_PATH="${ROUTE_PATH:-/collect}"
SHARED_SECRET="${SHARED_SECRET:-change_me}"
ALLOWED_HUB_IPS="${ALLOWED_HUB_IPS:-127.0.0.1,::1}"
REQUEST_TTL_SECONDS="${REQUEST_TTL_SECONDS:-120}"
MAX_BODY_BYTES="${MAX_BODY_BYTES:-1048576}"
NONCE_CACHE_FILE="${NONCE_CACHE_FILE:-/tmp/backup-agent-nonces.db}"

RCLONE_PUSH_SCRIPT="${RCLONE_PUSH_SCRIPT:-/opt/backup-dashboard/scripts/agent/linux_push_from_log.sh}"
DEFAULT_HUB_URL="${DEFAULT_HUB_URL:-http://127.0.0.1:8000}"
DEFAULT_INGEST_TOKEN="${DEFAULT_INGEST_TOKEN:-change_me_ingest}"
DEFAULT_NODE_NAME="${DEFAULT_NODE_NAME:-linux-node}"
DEFAULT_RCLONE_JOB_NAME="${DEFAULT_RCLONE_JOB_NAME:-nightly-share}"
DEFAULT_RCLONE_LOG_PATH="${DEFAULT_RCLONE_LOG_PATH:-/var/log/rclone_da_backup.log}"
DEFAULT_SOURCE_NAME="${DEFAULT_SOURCE_NAME:-rclone}"
DEFAULT_MAX_LINES="${DEFAULT_MAX_LINES:-5000}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKER_SCRIPT="${WORKER_SCRIPT:-${SCRIPT_DIR}/linux_secure_receiver_worker.sh}"

require_bin() {
  local b="${1:-}"
  if ! command -v "${b}" >/dev/null 2>&1; then
    echo "[agent] missing required binary: ${b}" >&2
    exit 1
  fi
}

if [[ -z "${SHARED_SECRET}" || "${SHARED_SECRET}" == "change_me" || ${#SHARED_SECRET} -lt 16 ]]; then
  echo "[agent] SHARED_SECRET is weak/missing. Use at least 16 random chars." >&2
  exit 1
fi

if [[ ! -x "${WORKER_SCRIPT}" ]]; then
  echo "[agent] worker script missing or not executable: ${WORKER_SCRIPT}" >&2
  exit 1
fi

require_bin socat
require_bin openssl
require_bin jq
require_bin curl

echo "[agent] listening on ${LISTEN_HOST}:${LISTEN_PORT}${ROUTE_PATH}"

export LISTEN_ROUTE_PATH="${ROUTE_PATH}"
export SHARED_SECRET
export ALLOWED_HUB_IPS
export REQUEST_TTL_SECONDS
export MAX_BODY_BYTES
export NONCE_CACHE_FILE
export RCLONE_PUSH_SCRIPT
export DEFAULT_HUB_URL
export DEFAULT_INGEST_TOKEN
export DEFAULT_NODE_NAME
export DEFAULT_RCLONE_JOB_NAME
export DEFAULT_RCLONE_LOG_PATH
export DEFAULT_SOURCE_NAME
export DEFAULT_MAX_LINES

exec socat "TCP-LISTEN:${LISTEN_PORT},bind=${LISTEN_HOST},reuseaddr,fork" "SYSTEM:bash ${WORKER_SCRIPT}"
