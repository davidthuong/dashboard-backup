#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 2 ]]; then
  echo "Usage: $0 <server_name> <mode>"
  echo "mode: http-only | https"
  exit 1
fi

SERVER_NAME="$1"
MODE="$2"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CONF_DIR="${ROOT_DIR}/deploy/nginx/conf.d"

if [[ "${MODE}" == "http-only" ]]; then
  TEMPLATE="${CONF_DIR}/backup-dashboard.http-only.conf.template"
elif [[ "${MODE}" == "https" ]]; then
  TEMPLATE="${CONF_DIR}/backup-dashboard.conf.template"
else
  echo "Unknown mode: ${MODE}"
  exit 1
fi

TARGET="${CONF_DIR}/backup-dashboard.conf"
sed "s|__SERVER_NAME__|${SERVER_NAME}|g" "${TEMPLATE}" > "${TARGET}"
echo "Rendered: ${TARGET} (${MODE})"

