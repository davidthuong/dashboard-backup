# Backup Dashboard MVP (Centralized)

Webapp de quan ly tap trung trang thai backup tu:
- Veeam Backup & Replication (VBR 12 REST API)
- rclone jobs (parse log hoac remote push status)
- DirectAdmin backup jobs (parse log hoac remote push status)

## Kien truc de nghi

1. Hub (server trung tam):
- Chay webapp nay.
- Luu lich su + hien thi dashboard.
- Canh bao Telegram/Email khi fail.

2. Nodes (backup servers):
- Cach A (pull): Hub doc truc tiep log qua share/mount path.
- Cach B (push): Node gui status ve Hub qua `POST /api/ingest/status`.

Khuyen nghi:
- Veeam: de Hub pull truc tiep tu tung VBR server.
- rclone tren nhieu node: dung push agent cho on dinh.

## Tinh nang MVP

- Dashboard latest status
- Filter/search theo source, status, job name
- History (SQLite)
- Polling interval tuy chinh tren UI
- Manual collect
- 1 admin account login
- Alert fail/warning qua Telegram/Email

## Chay local

```bash
cd "dashboard backup"
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux
source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env   # Windows
# cp .env.example .env   # Linux
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Mo: `http://localhost:8000`

## Cau hinh `.env`

Quan trong nhat:
- `ADMIN_USERNAME`, `ADMIN_PASSWORD`, `SESSION_SECRET`
- `POLLING_INTERVAL_SECONDS`
- `INGEST_ENABLED=true`
- `INGEST_API_TOKEN=<token-bao-mat>`
- Optional hub->node trigger:
  - `AGENT_TRIGGER_ENABLED=true`
  - `AGENT_NODES_JSON=[...]`

Veeam:
- Single server: dung `VEEAM_BASE_URL`, `VEEAM_USERNAME`, `VEEAM_PASSWORD`
- Set them `VEEAM_API_VERSION` (default `1.1-rev0` for VBR 12)
- Multi server: dung `VEEAM_TARGETS_JSON` (uu tien hon config single)

Vi du `VEEAM_TARGETS_JSON`:

```json
[
  {
    "name": "site-a",
    "base_url": "https://vbr-a:9419",
    "username": "readonly_a",
    "password": "pass_a",
    "verify_ssl": false
  },
  {
    "name": "site-b",
    "base_url": "https://vbr-b:9419",
    "username": "readonly_b",
    "password": "pass_b",
    "sessions_url": "https://vbr-b:9419/api/v1/jobs/states"
  }
]
```

rclone pull mode:
- `RCLONE_LOG_PATHS` ho tro:
  - `node::path` (local/mounted path)
  - `node::ssh://user@host:22/path/log` (hub SSH pull)
- Vi du:
  - `win-bk01::\\win-bk01\logs\rclone-nightly.log`
  - `linux-bk01::/mnt/logshare/linux-bk01/rclone.log`
  - `linux-bk02::ssh://root@10.10.10.22:22/var/log/rclone_da_backup.log`

DirectAdmin pull mode:
- `DIRECTADMIN_ENABLED=true`
- `DIRECTADMIN_LOG_PATHS` ho tro:
  - `node::path` (local/mounted path)
  - `node::ssh://user@host:22/path/log` (hub SSH pull)
- Vi du:
  - `da-node-01::/var/log/directadmin/backup.log`
  - `da-node-02::ssh://root@10.10.10.31:22/var/log/directadmin/backup.log`

## API ingest (node -> hub)

Endpoint:
- `POST /api/ingest/status`
- Header: `X-Ingest-Token: <INGEST_API_TOKEN>`

Body:

```json
{
  "items": [
    {
      "node": "linux-bk01",
      "source": "rclone",
      "job_name": "daily-home",
      "status": "success",
      "message": "errors=0",
      "ended_at": "2026-03-21T01:20:00+07:00"
    }
  ]
}
```

## API trigger agent (hub -> node)

- `GET /api/agents` (list configured nodes)
- `POST /api/agents/trigger-all`
- `POST /api/agents/trigger/{name}`

This mode uses HMAC signature + timestamp + nonce (anti-replay) on node receiver.

## Remote agent scripts

Thu muc: [scripts/agent/README.md](c:/Users/Admin/Documents/tool-work/dashboard backup/scripts/agent/README.md)

Them server (node) moi vao hub: [ADD_NODE.md](ADD_NODE.md)

Tu dong trigger agent hang ngay: `AGENT_AUTO_TRIGGER_TIMES=07:00` (gio theo `TIMEZONE`, nhieu gio cach nhau dau phay).

Kiem tra node tu hub:

```bash
docker exec backup-dashboard-app python -m app.agent_cli list
docker exec backup-dashboard-app python -m app.agent_cli health
docker exec backup-dashboard-app python -m app.agent_cli trigger <name>|--all
```

Co san:
- `scripts/agent/push_by_exit_code.py`
- `scripts/agent/report_rclone_log.py`
- `scripts/agent/report_directadmin_log.py`
- `scripts/agent/windows_icewarp_backup_and_push.ps1`
- `scripts/agent/linux_rclone_backup_and_push.sh`
- `scripts/agent/linux_push_from_log.sh`
- `scripts/agent/windows_rclone_backup_and_push.ps1`
- `scripts/agent/windows_push_from_log.ps1`
- `scripts/agent/windows_secure_receiver.ps1` (secure hub-trigger listener)
- `scripts/agent/windows_secure_receiver_runner.ps1` (auto-restart receiver)
- `scripts/agent/windows_install_secure_receiver_task.ps1` (install startup task + firewall/urlacl)
- `scripts/agent/windows_remove_secure_receiver_task.ps1` (remove startup task + cleanup)
- `scripts/agent/linux_secure_receiver.sh` (secure hub-trigger listener for Linux)
- `scripts/agent/linux_secure_receiver_runner.sh` (auto-restart receiver)
- `scripts/agent/linux_install_secure_receiver_service.sh` (install systemd service)
- `scripts/agent/linux_remove_secure_receiver_service.sh` (remove systemd service)

Khong muon dung Python:
- Dung truc tiep 4 script shell/PowerShell:
  - `linux_rclone_backup_and_push.sh`
  - `linux_push_from_log.sh`
  - `windows_rclone_backup_and_push.ps1`
  - `windows_push_from_log.ps1`

## Docker

```bash
docker build -t backup-dashboard .
docker run -d --name backup-dashboard -p 8000:8000 --env-file .env backup-dashboard
```

## Deploy production (Nginx + SSL)

Files added:
- `docker-compose.prod.yml`
- `deploy/nginx/conf.d/backup-dashboard.conf.template`
- `deploy/nginx/conf.d/backup-dashboard.http-only.conf.template`
- `scripts/deploy/bootstrap_ubuntu.sh`
- `scripts/deploy/init_ssl.sh`
- `scripts/deploy/renew_ssl.sh`
- `scripts/deploy/render_nginx_conf.sh`

Full step-by-step:
- [DEPLOY_PROD.md](c:/Users/Admin/Documents/tool-work/dashboard backup/DEPLOY_PROD.md)

Quick start for your server `103.238.213.14`:

```bash
ssh root@103.238.213.14
cd /opt/backup-dashboard
bash scripts/deploy/bootstrap_ubuntu.sh
cp .env.prod.example .env
nano .env
chmod +x scripts/deploy/*.sh
bash scripts/deploy/init_ssl.sh 103.238.213.14
docker compose -f docker-compose.prod.yml up -d --build
```

## API chinh

- `POST /api/jobs/collect`
- `GET /api/jobs/latest`
- `GET /api/jobs/history`
- `GET /api/settings/polling`
- `PUT /api/settings/polling`
- `POST /api/ingest/status`
