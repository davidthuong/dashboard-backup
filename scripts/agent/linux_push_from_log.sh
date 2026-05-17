#!/usr/bin/env bash
set -euo pipefail

# Fill these values for your node.
HUB_URL="${HUB_URL:-http://10.10.10.10:8000}"
INGEST_TOKEN="${INGEST_TOKEN:-change_me}"
NODE_NAME="${NODE_NAME:-linux-bk01}"
JOB_NAME="${JOB_NAME:-daily-home}"
LOG_PATH="${LOG_PATH:-/var/log/rclone/daily-home.log}"
SOURCE_NAME="${SOURCE_NAME:-rclone}"
MAX_LINES="${MAX_LINES:-5000}"

json_escape() {
  sed -e 's/\\/\\\\/g' -e 's/"/\\"/g'
}

to_iso_naive() {
  local ts="${1:-}"
  if [[ -z "${ts}" ]]; then
    printf ""
    return 0
  fi
  printf "%s" "${ts}" | sed 's/ /T/'
}

if [[ ! -f "${LOG_PATH}" ]]; then
  STATUS="failed"
  MSG="log file not found: ${LOG_PATH}"
  LAST_ERROR_LINE="${MSG}"
  ERRORS_LINE=""
  TAIL10_JSON='[]'
else
  TAIL_BLOCK="$(tail -n "${MAX_LINES}" "${LOG_PATH}" || true)"

  MARKER_ROWS="$(printf "%s\n" "${TAIL_BLOCK}" | awk '
BEGIN { OFS="\t" }
{
  line = $0
  if (line ~ /^\[[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}\] (START|SUCCESS|FAILED) backup user=/) {
    ts = substr(line, 2, 19)
    rest = substr(line, 23)
    event = ""
    if (rest ~ /^START backup user=/) event = "START"
    else if (rest ~ /^SUCCESS backup user=/) event = "SUCCESS"
    else if (rest ~ /^FAILED backup user=/) event = "FAILED"
    if (event == "") next

    userpart = rest
    sub(/^(START|SUCCESS|FAILED) backup user=/, "", userpart)
    user = userpart
    sub(/ .*/, "", user)
    if (user == "") user = "unknown-user"

    detail = rest
    sub(/^(START|SUCCESS|FAILED) backup user=[^ ]+ ?/, "", detail)
    source_file = ""
    target_path = ""
    exit_code = ""
    reason = ""

    seen[user] = 1
    if (event == "START") {
      start[user] = ts
      if (match(detail, /file=[^ ]+/)) {
        source_file = substr(detail, RSTART + 5, RLENGTH - 5)
        start_file[user] = source_file
      }
      next
    }

    if (user in start_file) source_file = start_file[user]
    if (match(detail, /->[[:space:]]*.*/)) {
      target_path = detail
      sub(/^->[[:space:]]*/, "", target_path)
    }
    if (match(detail, /exit_code=[0-9]+/)) {
      exit_code = substr(detail, RSTART + 10, RLENGTH - 10)
    }
    if (match(detail, /reason=[^ ]+/)) {
      reason = substr(detail, RSTART + 7, RLENGTH - 7)
    }

    latest_status[user] = (event == "SUCCESS" ? "success" : "failed")
    latest_start[user] = (user in start ? start[user] : "")
    latest_end[user] = ts
    latest_source_file[user] = source_file
    latest_target_path[user] = target_path
    latest_exit_code[user] = exit_code
    latest_reason[user] = reason

    if (event == "SUCCESS") {
      if (source_file != "" && target_path != "") latest_msg[user] = source_file " -> " target_path
      else if (target_path != "") latest_msg[user] = "-> " target_path
      else latest_msg[user] = (detail != "" ? detail : "backup completed")
    } else {
      if (source_file != "" && exit_code != "") latest_msg[user] = source_file "; exit_code=" exit_code
      else latest_msg[user] = (detail != "" ? detail : "backup failed")
    }
    delete start[user]
    delete start_file[user]
  }
}
END {
  for (u in seen) {
    if (u in latest_status) {
      print u, latest_status[u], latest_start[u], latest_end[u], latest_source_file[u], latest_target_path[u], latest_exit_code[u], latest_reason[u], latest_msg[u]
    } else if (u in start) {
      sfile = (u in start_file ? start_file[u] : "")
      msg = (sfile != "" ? "in progress: " sfile : "backup in progress")
      print u, "running", start[u], "", sfile, "", "", "", msg
    }
  }
}
')"

  if [[ -n "${MARKER_ROWS}" ]]; then
    LOG_ESC="$(printf "%s" "${LOG_PATH}" | json_escape)"
    ITEMS_JSON=""
    while IFS=$'\t' read -r user status started ended source_file target_path exit_code reason msg; do
      [[ -z "${user}" ]] && continue
      started_iso="$(to_iso_naive "${started}")"
      ended_iso="$(to_iso_naive "${ended}")"
      job_name="${JOB_NAME}:${user}"

      JOB_ESC="$(printf "%s" "${job_name}" | json_escape)"
      MSG_ESC="$(printf "%s" "${msg}" | json_escape)"
      USER_ESC="$(printf "%s" "${user}" | json_escape)"
      SOURCE_FILE_ESC="$(printf "%s" "${source_file}" | json_escape)"
      TARGET_PATH_ESC="$(printf "%s" "${target_path}" | json_escape)"
      EXIT_CODE_ESC="$(printf "%s" "${exit_code}" | json_escape)"
      REASON_ESC="$(printf "%s" "${reason}" | json_escape)"
      STARTED_FIELD="null"
      ENDED_FIELD="null"
      if [[ -n "${started_iso}" ]]; then
        STARTED_FIELD="\"${started_iso}\""
      fi
      if [[ -n "${ended_iso}" ]]; then
        ENDED_FIELD="\"${ended_iso}\""
      fi

      item="$(cat <<EOF
{
  "node": "${NODE_NAME}",
  "source": "${SOURCE_NAME}",
  "job_name": "${JOB_ESC}",
  "status": "${status}",
  "message": "${MSG_ESC}",
  "started_at": ${STARTED_FIELD},
  "ended_at": ${ENDED_FIELD},
  "raw_payload": {
    "log_path": "${LOG_ESC}",
    "format": "da_marker",
    "user": "${USER_ESC}",
    "source_file": "${SOURCE_FILE_ESC}",
    "target_path": "${TARGET_PATH_ESC}",
    "exit_code": "${EXIT_CODE_ESC}",
    "reason": "${REASON_ESC}"
  }
}
EOF
)"
      if [[ -n "${ITEMS_JSON}" ]]; then
        ITEMS_JSON="${ITEMS_JSON},${item}"
      else
        ITEMS_JSON="${item}"
      fi
    done <<< "${MARKER_ROWS}"

    PAYLOAD="$(cat <<EOF
{
  "items": [${ITEMS_JSON}]
}
EOF
)"

    curl -sS -X POST "${HUB_URL%/}/api/ingest/status" \
      -H "X-Ingest-Token: ${INGEST_TOKEN}" \
      -H "Content-Type: application/json" \
      -d "${PAYLOAD}"
    exit 0
  fi

  LAST_ERROR_LINE="$(printf "%s\n" "${TAIL_BLOCK}" | grep -Ei '\bERROR\b|failed to|fatal' | tail -n 1 || true)"
  ERRORS_LINE="$(printf "%s\n" "${TAIL_BLOCK}" | grep -Ei 'Errors:\s*[0-9]+' | tail -n 1 || true)"

  ERRORS_COUNT=""
  if [[ -n "${ERRORS_LINE}" ]]; then
    ERRORS_COUNT="$(printf "%s" "${ERRORS_LINE}" | sed -nE 's/.*[Ee]rrors:[[:space:]]*([0-9]+).*/\1/p' | tail -n 1)"
  fi

  STATUS="unknown"
  MSG="cannot infer final status from log"
  if [[ -n "${ERRORS_COUNT}" && "${ERRORS_COUNT}" == "0" ]]; then
    STATUS="success"
    MSG="errors: 0"
  elif [[ -n "${LAST_ERROR_LINE}" ]]; then
    STATUS="failed"
    MSG="${LAST_ERROR_LINE}"
  elif printf "%s\n" "${TAIL_BLOCK}" | grep -qiE 'Transferred:|Elapsed time:'; then
    STATUS="success"
    MSG="transfer summary detected"
  fi

  TAIL10_JSON="$(
    tail -n 10 "${LOG_PATH}" | sed 's/\r$//' | while IFS= read -r line; do
      esc="$(printf "%s" "${line}" | json_escape)"
      printf "\"%s\",\n" "${esc}"
    done | sed '$ s/,$//' | awk 'BEGIN{print "["} {print} END{print "]"}'
  )"
fi

ENDED_AT="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
MSG_ESC="$(printf "%s" "${MSG}" | json_escape)"
ERR_ESC="$(printf "%s" "${ERRORS_LINE}" | json_escape)"
LAST_ERR_ESC="$(printf "%s" "${LAST_ERROR_LINE}" | json_escape)"
LOG_ESC="$(printf "%s" "${LOG_PATH}" | json_escape)"

PAYLOAD="$(cat <<EOF
{
  "items": [
    {
      "node": "${NODE_NAME}",
      "source": "${SOURCE_NAME}",
      "job_name": "${JOB_NAME}",
      "status": "${STATUS}",
      "message": "${MSG_ESC}",
      "ended_at": "${ENDED_AT}",
      "raw_payload": {
        "log_path": "${LOG_ESC}",
        "errors_line": "${ERR_ESC}",
        "last_error_line": "${LAST_ERR_ESC}",
        "last_10_log_lines": ${TAIL10_JSON}
      }
    }
  ]
}
EOF
)"

curl -sS -X POST "${HUB_URL%/}/api/ingest/status" \
  -H "X-Ingest-Token: ${INGEST_TOKEN}" \
  -H "Content-Type: application/json" \
  -d "${PAYLOAD}"
