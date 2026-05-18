#!/usr/bin/env bash
set -euo pipefail

# Per-request worker for linux_secure_receiver.sh (invoked by socat SYSTEM).

LISTEN_ROUTE_PATH="${LISTEN_ROUTE_PATH:-/collect}"
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

trim_cr() {
  local s="${1:-}"
  printf "%s" "${s%$'\r'}"
}

json_escape() {
  sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' -e ':a;N;$!ba;s/\n/\\n/g'
}

send_json() {
  local code="${1:-500}"
  local body="${2:-{\"ok\":false,\"error\":\"internal\"}}"
  local reason="OK"
  case "${code}" in
    200) reason="OK" ;;
    400) reason="Bad Request" ;;
    401) reason="Unauthorized" ;;
    403) reason="Forbidden" ;;
    404) reason="Not Found" ;;
    405) reason="Method Not Allowed" ;;
    413) reason="Payload Too Large" ;;
    415) reason="Unsupported Media Type" ;;
    500) reason="Internal Server Error" ;;
  esac

  local body_len
  body_len=$(printf "%s" "${body}" | wc -c | tr -d ' ')
  printf "HTTP/1.1 %s %s\r\n" "${code}" "${reason}"
  printf "Content-Type: application/json; charset=utf-8\r\n"
  printf "Content-Length: %s\r\n" "${body_len}"
  printf "Connection: close\r\n"
  printf "\r\n"
  printf "%s" "${body}"
}

safe_error() {
  local msg="${1:-error}"
  local esc
  esc=$(printf "%s" "${msg}" | json_escape)
  send_json 500 "{\"ok\":false,\"error\":\"${esc}\"}"
  exit 0
}

const_time_equals() {
  local a="${1:-}"
  local b="${2:-}"
  if [[ ${#a} -ne ${#b} ]]; then
    return 1
  fi
  local i diff=0
  for ((i = 0; i < ${#a}; i++)); do
    local ca cb
    ca=$(printf "%d" "'${a:i:1}")
    cb=$(printf "%d" "'${b:i:1}")
    diff=$((diff | (ca ^ cb)))
  done
  [[ ${diff} -eq 0 ]]
}

cleanup_old_nonces() {
  local now_ts="${1:-0}"
  local ttl="${2:-120}"
  local keep_after=$((now_ts - ttl))
  touch "${NONCE_CACHE_FILE}"
  awk -F'\t' -v min_ts="${keep_after}" 'NF>=2 { if ($2 >= min_ts) print $0 }' "${NONCE_CACHE_FILE}" > "${NONCE_CACHE_FILE}.tmp" || true
  mv -f "${NONCE_CACHE_FILE}.tmp" "${NONCE_CACHE_FILE}"
}

is_replay_nonce() {
  local nonce="${1:-}"
  grep -Fq "${nonce}" "${NONCE_CACHE_FILE}" 2>/dev/null
}

remember_nonce() {
  local nonce="${1:-}"
  local now_ts="${2:-0}"
  printf "%s\t%s\n" "${nonce}" "${now_ts}" >> "${NONCE_CACHE_FILE}"
}

header_get() {
  local name="${1:-}"
  local line
  for line in "${HEADERS[@]}"; do
    if [[ "${line,,}" == "${name,,}:"* ]]; then
      printf "%s" "${line#*: }"
      return 0
    fi
  done
  return 1
}

require_bin() {
  local b="${1:-}"
  if ! command -v "${b}" >/dev/null 2>&1; then
    safe_error "required binary not found: ${b}"
  fi
}

extract_json_with_jq() {
  local input="${1:-}"
  local expr="${2:-}"
  printf "%s" "${input}" | jq -r "${expr} // empty" 2>/dev/null || true
}

run_rclone_log_push() {
  local payload_json="${1:-{}}"

  if [[ ! -x "${RCLONE_PUSH_SCRIPT}" ]]; then
    safe_error "rclone push script not found or not executable: ${RCLONE_PUSH_SCRIPT}"
  fi

  require_bin jq

  local hub_url ingest_token node_name job_name log_path source_name max_lines
  hub_url="$(extract_json_with_jq "${payload_json}" '.hub_url')"
  ingest_token="$(extract_json_with_jq "${payload_json}" '.ingest_token')"
  node_name="$(extract_json_with_jq "${payload_json}" '.node_name')"
  job_name="$(extract_json_with_jq "${payload_json}" '.job_name')"
  log_path="$(extract_json_with_jq "${payload_json}" '.log_path')"
  source_name="$(extract_json_with_jq "${payload_json}" '.source_name')"
  max_lines="$(extract_json_with_jq "${payload_json}" '.max_lines')"

  [[ -n "${hub_url}" ]] || hub_url="${DEFAULT_HUB_URL}"
  [[ -n "${ingest_token}" ]] || ingest_token="${DEFAULT_INGEST_TOKEN}"
  [[ -n "${node_name}" ]] || node_name="${DEFAULT_NODE_NAME}"
  [[ -n "${job_name}" ]] || job_name="${DEFAULT_RCLONE_JOB_NAME}"
  [[ -n "${log_path}" ]] || log_path="${DEFAULT_RCLONE_LOG_PATH}"
  [[ -n "${source_name}" ]] || source_name="${DEFAULT_SOURCE_NAME}"
  [[ -n "${max_lines}" ]] || max_lines="${DEFAULT_MAX_LINES}"

  set +e
  local output
  output="$(
    HUB_URL="${hub_url}" \
    INGEST_TOKEN="${ingest_token}" \
    NODE_NAME="${node_name}" \
    JOB_NAME="${job_name}" \
    LOG_PATH="${log_path}" \
    SOURCE_NAME="${source_name}" \
    MAX_LINES="${max_lines}" \
    "${RCLONE_PUSH_SCRIPT}" 2>&1
  )"
  local exit_code=$?
  set -e

  local out_esc
  out_esc=$(printf "%s" "${output}" | tail -n 40 | json_escape)
  send_json 200 "{\"ok\":true,\"result\":{\"action\":\"rclone_log_push\",\"exit_code\":${exit_code},\"output\":\"${out_esc}\"}}"
  exit 0
}

main() {
  require_bin openssl

  local request_line
  IFS= read -r request_line || { send_json 400 "{\"ok\":false,\"error\":\"empty request\"}"; exit 0; }
  request_line="$(trim_cr "${request_line}")"

  local method path _httpver
  method="$(printf "%s" "${request_line}" | awk '{print $1}')"
  path="$(printf "%s" "${request_line}" | awk '{print $2}')"
  _httpver="$(printf "%s" "${request_line}" | awk '{print $3}')"

  if [[ "${method}" != "POST" ]]; then
    send_json 405 "{\"ok\":false,\"error\":\"Method not allowed\"}"
    exit 0
  fi
  if [[ -z "${path}" || "${path}" != "${LISTEN_ROUTE_PATH}" ]]; then
    send_json 404 "{\"ok\":false,\"error\":\"Not found\"}"
    exit 0
  fi

  local remote_ip="${SOCAT_PEERADDR:-unknown}"
  local allowed=0
  IFS=',' read -r -a allowlist <<< "${ALLOWED_HUB_IPS}"
  if [[ ${#allowlist[@]} -eq 0 ]]; then
    allowed=1
  else
    local ip
    for ip in "${allowlist[@]}"; do
      ip="$(echo "${ip}" | xargs)"
      if [[ -n "${ip}" && "${ip}" == "${remote_ip}" ]]; then
        allowed=1
        break
      fi
    done
  fi
  if [[ ${allowed} -ne 1 ]]; then
    send_json 403 "{\"ok\":false,\"error\":\"Remote IP not allowed\"}"
    exit 0
  fi

  HEADERS=()
  local line
  while IFS= read -r line; do
    line="$(trim_cr "${line}")"
    [[ -z "${line}" ]] && break
    HEADERS+=("${line}")
  done

  local content_length content_type
  content_length="$(header_get "Content-Length" || true)"
  content_type="$(header_get "Content-Type" || true)"
  local ts_raw nonce sig
  ts_raw="$(header_get "X-Agent-Timestamp" || true)"
  nonce="$(header_get "X-Agent-Nonce" || true)"
  sig="$(header_get "X-Agent-Signature" || true)"

  content_length="${content_length:-0}"
  [[ "${content_length}" =~ ^[0-9]+$ ]] || content_length=0
  if (( content_length > MAX_BODY_BYTES )); then
    send_json 413 "{\"ok\":false,\"error\":\"Request body too large\"}"
    exit 0
  fi
  if [[ "${content_type,,}" != application/json* ]]; then
    send_json 415 "{\"ok\":false,\"error\":\"Unsupported content type\"}"
    exit 0
  fi

  local body=""
  if (( content_length > 0 )); then
    body="$(dd bs=1 count="${content_length}" 2>/dev/null)"
  fi

  if [[ -z "${ts_raw}" || -z "${nonce}" || -z "${sig}" ]]; then
    send_json 401 "{\"ok\":false,\"error\":\"Missing security headers\"}"
    exit 0
  fi
  if ! [[ "${ts_raw}" =~ ^[0-9]+$ ]]; then
    send_json 401 "{\"ok\":false,\"error\":\"Invalid timestamp\"}"
    exit 0
  fi

  local now_ts
  now_ts="$(date +%s)"
  local diff=$((now_ts - ts_raw))
  if (( diff < 0 )); then diff=$(( -diff )); fi
  if (( diff > REQUEST_TTL_SECONDS )); then
    send_json 401 "{\"ok\":false,\"error\":\"Request expired\"}"
    exit 0
  fi

  cleanup_old_nonces "${now_ts}" "${REQUEST_TTL_SECONDS}"
  if is_replay_nonce "${nonce}"; then
    send_json 401 "{\"ok\":false,\"error\":\"Replay detected\"}"
    exit 0
  fi

  local signing_input="${ts_raw}.${nonce}.${body}"
  local expected_sig
  expected_sig="$(printf "%s" "${signing_input}" | openssl dgst -sha256 -hmac "${SHARED_SECRET}" -binary | xxd -p -c 256)"
  if ! const_time_equals "${expected_sig}" "${sig}"; then
    send_json 401 "{\"ok\":false,\"error\":\"Invalid signature\"}"
    exit 0
  fi
  remember_nonce "${nonce}" "${now_ts}"

  require_bin jq
  local action payload_json
  action="$(printf "%s" "${body}" | jq -r '.action // empty' 2>/dev/null || true)"
  payload_json="$(printf "%s" "${body}" | jq -c '.payload // {}' 2>/dev/null || true)"
  [[ -n "${payload_json}" ]] || payload_json='{}'

  case "${action}" in
    health)
      send_json 200 "{\"ok\":true,\"result\":{\"action\":\"health\",\"time\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"}}"
      ;;
    rclone_log_push)
      run_rclone_log_push "${payload_json}"
      ;;
    *)
      send_json 400 "{\"ok\":false,\"error\":\"Unsupported action\"}"
      ;;
  esac
}

main "$@"
