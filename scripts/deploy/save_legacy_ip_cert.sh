#!/usr/bin/env bash
# Before switching the hub to a domain: copy the certificate nginx serves today (self-signed, by IP)
# into deploy/letsencrypt/legacy-ip/. render_nginx_conf.sh then keeps https://<IP> working with
# that same certificate, so nodes that still use the IP (legacy receivers) are not cut off.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CONF="${ROOT_DIR}/deploy/nginx/conf.d/backup-dashboard.conf"
LE_DIR="${ROOT_DIR}/deploy/letsencrypt"
DEST="${LE_DIR}/legacy-ip"

if [[ -e "${DEST}/fullchain.pem" ]]; then
  echo "[SKIP] ${DEST} already exists, not overwriting."
  exit 0
fi
if [[ ! -f "${CONF}" ]]; then
  echo "[ERROR] ${CONF} not found (nginx config was never rendered?)"
  exit 1
fi

# nginx paths are inside the container: /etc/letsencrypt/... == deploy/letsencrypt/... on the host.
host_path() {
  local directive="$1" path
  path="$(grep -m1 -E "^\s*${directive}\s" "${CONF}" | awk '{print $2}' | tr -d ';')"
  if [[ "${path}" != /etc/letsencrypt/* ]]; then
    echo "[ERROR] ${directive} '${path}' is not under /etc/letsencrypt" >&2
    exit 1
  fi
  echo "${LE_DIR}${path#/etc/letsencrypt}"
}

CERT="$(host_path ssl_certificate)"
KEY="$(host_path ssl_certificate_key)"

mkdir -p "${DEST}"
cp -L "${CERT}" "${DEST}/fullchain.pem"
cp -L "${KEY}" "${DEST}/privkey.pem"
chmod 600 "${DEST}/privkey.pem"

echo "[OK] Saved current certificate to ${DEST}:"
openssl x509 -noout -subject -enddate -fingerprint -sha1 -in "${DEST}/fullchain.pem"
