# Deploy Production (Nginx + SSL)

Target server example: `103.238.214.35`

## 1) Copy project to server

From local machine:

```bash
scp -r "dashboard backup" root@103.238.214.35:/opt/backup-dashboard
```

On server:

```bash
ssh root@103.238.214.35
cd /opt/backup-dashboard
```

## 2) Install runtime packages

```bash
bash scripts/deploy/bootstrap_ubuntu.sh
```

## 3) Configure app env

```bash
cp .env.prod.example .env
nano .env
```

`DATABASE_URL` is ignored here: `docker-compose.prod.yml` keeps SQLite at `./data/backup_dashboard.db`
(host `/opt/backup-dashboard/data`), the only place that survives `up -d --build`. Back up that folder.

Must change:
- `ADMIN_PASSWORD`
- `SESSION_SECRET`
- `INGEST_API_TOKEN`
- `VEEAM_*` or `VEEAM_TARGETS_JSON`

## 4) Create SSL + nginx config

Case A: You only have IP (`103.238.214.35`)
- Lets Encrypt cannot issue cert for IP.
- Use self-signed cert:

```bash
chmod +x scripts/deploy/*.sh
bash scripts/deploy/init_ssl.sh 103.238.214.35
docker compose -f docker-compose.prod.yml up -d --build
```

Open dashboard:
- `https://103.238.214.35` (browser will show cert warning, accept once)

Case B: You have domain (recommended)
- Point DNS A record to `103.238.214.35`, ex: `backup.example.com`.
- Then run:

```bash
chmod +x scripts/deploy/*.sh
bash scripts/deploy/init_ssl.sh backup.example.com admin@example.com
docker compose -f docker-compose.prod.yml up -d --build
```

Open dashboard:
- `https://backup.example.com`

### Hub đang chạy bằng IP → chuyển sang domain

DNS A record của domain phải trỏ về hub, cổng 80 + 443 mở từ Internet. Chạy trên hub (`cd /opt/backup-dashboard`):

```bash
git pull
bash scripts/deploy/save_legacy_ip_cert.sh            # giữ https://<IP> + chứng chỉ cũ cho node cũ
bash scripts/deploy/init_ssl.sh backup.sys.bizmac.io <email-nhan-thong-bao-letsencrypt>
sed -i 's#^AGENT_HUB_URL=.*#AGENT_HUB_URL=https://backup.sys.bizmac.io#' .env
docker compose -f docker-compose.prod.yml up -d --force-recreate app
```

Kiểm tra (domain → Let's Encrypt, IP → chứng chỉ cũ):

```bash
echo | openssl s_client -connect 127.0.0.1:443 -servername backup.sys.bizmac.io 2>/dev/null | openssl x509 -noout -subject -issuer -enddate
echo | openssl s_client -connect 127.0.0.1:443 2>/dev/null | openssl x509 -noout -subject -issuer -enddate
```

Khi không còn node nào dùng IP (hub1–3 đã sang pull agent với domain):

```bash
rm -rf deploy/letsencrypt/legacy-ip
bash scripts/deploy/render_nginx_conf.sh backup.sys.bizmac.io https
docker compose -f docker-compose.prod.yml exec nginx nginx -s reload
```

## 5) Auto renew (domain mode)

Add cron:

```bash
crontab -e
```

```cron
0 3 * * * /opt/backup-dashboard/scripts/deploy/renew_ssl.sh >> /var/log/backup-dashboard-renew.log 2>&1
```

## 6) Verify services

```bash
docker compose -f docker-compose.prod.yml ps
docker compose -f docker-compose.prod.yml logs -f nginx
docker compose -f docker-compose.prod.yml logs -f app
```

