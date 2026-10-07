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

