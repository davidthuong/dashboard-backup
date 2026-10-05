#!/usr/bin/env bash
set -euo pipefail

# Keep linux_secure_receiver.sh alive.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RECEIVER_SCRIPT="${RECEIVER_SCRIPT:-${SCRIPT_DIR}/linux_secure_receiver.sh}"
RESTART_DELAY_SECONDS="${RESTART_DELAY_SECONDS:-3}"

if [[ ! -x "${RECEIVER_SCRIPT}" ]]; then
  echo "[runner] receiver script missing or not executable: ${RECEIVER_SCRIPT}" >&2
  exit 1
fi

while true; do
  echo "[runner] starting receiver..."
  set +e
  "${RECEIVER_SCRIPT}"
  code=$?
  set -e
  echo "[runner] receiver exited with code ${code}. Restarting in ${RESTART_DELAY_SECONDS}s..."
  sleep "${RESTART_DELAY_SECONDS}"
done
