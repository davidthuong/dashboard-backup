#!/usr/bin/env bash
set -euo pipefail

# Install systemd service for linux secure receiver runner.

SERVICE_NAME="${SERVICE_NAME:-backuphub-secure-receiver}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNNER_SCRIPT="${RUNNER_SCRIPT:-${SCRIPT_DIR}/linux_secure_receiver_runner.sh}"
ENV_FILE="${ENV_FILE:-${SCRIPT_DIR}/linux_secure_receiver.env}"

if [[ $EUID -ne 0 ]]; then
  echo "Run as root."
  exit 1
fi

if [[ ! -x "${RUNNER_SCRIPT}" ]]; then
  echo "Runner script missing/executable not set: ${RUNNER_SCRIPT}" >&2
  exit 1
fi

if [[ ! -f "${ENV_FILE}" ]]; then
  cat > "${ENV_FILE}" <<EOF
# Required
SHARED_SECRET=change_me_to_long_random_secret
ALLOWED_HUB_IPS=127.0.0.1
DEFAULT_HUB_URL=http://127.0.0.1:8000
DEFAULT_INGEST_TOKEN=change_me_ingest
DEFAULT_NODE_NAME=linux-node
DEFAULT_RCLONE_JOB_NAME=nightly-share
DEFAULT_RCLONE_LOG_PATH=/var/log/rclone_da_backup.log
RCLONE_PUSH_SCRIPT=/opt/backup-dashboard/scripts/agent/linux_push_from_log.sh

# Optional
LISTEN_HOST=0.0.0.0
LISTEN_PORT=9189
ROUTE_PATH=/collect
REQUEST_TTL_SECONDS=120
MAX_BODY_BYTES=1048576
ALLOWED_ACTIONS=health,rclone_log_push
ALLOWED_LOG_ROOTS=/var/log,/opt/backup-logs
ALLOW_INSECURE_HUB_URL=false
DEFAULT_SOURCE_NAME=rclone
DEFAULT_MAX_LINES=5000
NONCE_CACHE_FILE=/tmp/backup-agent-nonces.db
EOF
  chmod 600 "${ENV_FILE}"
  echo "Created env file: ${ENV_FILE}. Edit it before start."
fi

UNIT_PATH="/etc/systemd/system/${SERVICE_NAME}.service"
cat > "${UNIT_PATH}" <<EOF
[Unit]
Description=BackupHub Linux Secure Receiver
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
EnvironmentFile=${ENV_FILE}
ExecStart=/usr/bin/env bash ${RUNNER_SCRIPT}
Restart=always
RestartSec=2
User=root
WorkingDirectory=${SCRIPT_DIR}

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now "${SERVICE_NAME}"
systemctl status "${SERVICE_NAME}" --no-pager || true
echo "Installed service: ${SERVICE_NAME}"
