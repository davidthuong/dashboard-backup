#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <server_name> [email]"
  echo "Examples:"
  echo "  $0 backup.example.com admin@example.com"
  echo "  $0 103.238.214.35"
  exit 1
fi

SERVER_NAME="$1"
EMAIL="${2:-}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LE_DIR="${ROOT_DIR}/deploy/letsencrypt"

mkdir -p "${LE_DIR}"
mkdir -p "${ROOT_DIR}/deploy/nginx/certbot"

is_ip() {
  [[ "$1" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]
}

if is_ip "${SERVER_NAME}"; then
  echo "[INFO] Detected IP address. Let's Encrypt does not issue certs for IP."
  echo "[INFO] Generating self-signed certificate for ${SERVER_NAME}."

  mkdir -p "${LE_DIR}/live/${SERVER_NAME}"
  openssl req -x509 -nodes -newkey rsa:2048 -days 365 \
    -keyout "${LE_DIR}/live/${SERVER_NAME}/privkey.pem" \
    -out "${LE_DIR}/live/${SERVER_NAME}/fullchain.pem" \
    -subj "/CN=${SERVER_NAME}"

  "${ROOT_DIR}/scripts/deploy/render_nginx_conf.sh" "${SERVER_NAME}" "https"
  echo "[INFO] Self-signed cert created."
  echo "[INFO] Start services: docker compose -f docker-compose.prod.yml up -d --build"
  exit 0
fi

if [[ -z "${EMAIL}" ]]; then
  echo "Email is required for Let's Encrypt domain certificate."
  exit 1
fi

echo "[INFO] Preparing HTTP-only nginx config for ACME challenge."
"${ROOT_DIR}/scripts/deploy/render_nginx_conf.sh" "${SERVER_NAME}" "http-only"

echo "[INFO] Starting app + nginx (HTTP only)."
docker compose -f "${ROOT_DIR}/docker-compose.prod.yml" up -d --build app nginx

echo "[INFO] Requesting Let's Encrypt certificate for ${SERVER_NAME}."
docker compose -f "${ROOT_DIR}/docker-compose.prod.yml" run --rm certbot certonly \
  --webroot -w /var/www/certbot \
  --email "${EMAIL}" \
  -d "${SERVER_NAME}" \
  --agree-tos \
  --no-eff-email \
  --force-renewal

echo "[INFO] Switching nginx to HTTPS config."
"${ROOT_DIR}/scripts/deploy/render_nginx_conf.sh" "${SERVER_NAME}" "https"
docker compose -f "${ROOT_DIR}/docker-compose.prod.yml" up -d nginx

echo "[OK] SSL initialization complete."

