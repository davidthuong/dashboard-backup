#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

docker compose -f "${ROOT_DIR}/docker-compose.prod.yml" run --rm certbot renew
docker compose -f "${ROOT_DIR}/docker-compose.prod.yml" exec nginx nginx -s reload

echo "[OK] Certificate renew done and nginx reloaded."

