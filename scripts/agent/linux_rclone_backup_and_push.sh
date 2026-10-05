#!/usr/bin/env bash
set -euo pipefail

# Fill these values for your node.
HUB_URL="${HUB_URL:-http://10.10.10.10:8000}"
INGEST_TOKEN="${INGEST_TOKEN:-change_me}"
NODE_NAME="${NODE_NAME:-linux-bk01}"
JOB_NAME="${JOB_NAME:-daily-home}"
RCLONE_CMD="${RCLONE_CMD:-rclone sync /data remote:backup-home --log-file /var/log/rclone/daily-home.log --log-level INFO}"
SOURCE_NAME="${SOURCE_NAME:-rclone}"

echo "[INFO] Running backup job: ${JOB_NAME}"
set +e
bash -lc "${RCLONE_CMD}"
EXIT_CODE=$?
set -e

STATUS="success"
MSG="backup command executed"
if [[ ${EXIT_CODE} -ne 0 ]]; then
  STATUS="failed"
  MSG="backup command failed (exit_code=${EXIT_CODE})"
fi

ENDED_AT="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

PAYLOAD="$(cat <<EOF
{
  "items": [
    {
      "node": "${NODE_NAME}",
      "source": "${SOURCE_NAME}",
      "job_name": "${JOB_NAME}",
      "status": "${STATUS}",
      "message": "${MSG}",
      "ended_at": "${ENDED_AT}",
      "raw_payload": {
        "exit_code": ${EXIT_CODE}
      }
    }
  ]
}
EOF
)"

curl -sS -X POST "${HUB_URL%/}/api/ingest/status" \
  -H "X-Ingest-Token: ${INGEST_TOKEN}" \
  -H "Content-Type: application/json" \
  -d "${PAYLOAD}" >/dev/null || true

exit ${EXIT_CODE}
