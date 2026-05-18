#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="${SERVICE_NAME:-backuphub-secure-receiver}"
REMOVE_ENV_FILE="${REMOVE_ENV_FILE:-false}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${ENV_FILE:-${SCRIPT_DIR}/linux_secure_receiver.env}"

if [[ $EUID -ne 0 ]]; then
  echo "Run as root."
  exit 1
fi

systemctl disable --now "${SERVICE_NAME}" 2>/dev/null || true
rm -f "/etc/systemd/system/${SERVICE_NAME}.service"
systemctl daemon-reload

if [[ "${REMOVE_ENV_FILE}" == "true" ]]; then
  rm -f "${ENV_FILE}"
fi

echo "Removed service: ${SERVICE_NAME}"
