# Remote Agent Scripts (Linux + Windows)

These scripts help remote backup servers push status to the central dashboard (`/api/ingest/status`).

## 0) Quick Start (NEW - Hub Trigger)

If you want **hub click -> node run backup -> node push result** (no SSH, no cron), do this:

1. Pull latest code on hub and nodes:
```bash
cd /opt/backup-dashboard
git pull origin main
```

2. Hub `.env`:
- `AGENT_TRIGGER_ENABLED=true`
- `INGEST_ENABLED=true`
- set `AGENT_NODES_JSON` with node URL `/collect` + `shared_secret`

3. Windows node:
- copy/update `scripts/agent/*`
- install receiver task:
```powershell
powershell -ExecutionPolicy Bypass -File C:\backup-dashboard\scripts\agent\windows_install_secure_receiver_task.ps1 -SharedSecret "<shared-secret>" -AllowedHubIPs "<hub-ip>"
```

4. Linux node:
- install receiver service:
```bash
sudo apt update && sudo apt install -y socat openssl jq curl
sudo SERVICE_NAME=backuphub-secure-receiver ENV_FILE=/opt/backup-dashboard/scripts/agent/linux_secure_receiver.env RUNNER_SCRIPT=/opt/backup-dashboard/scripts/agent/linux_secure_receiver_runner.sh /opt/backup-dashboard/scripts/agent/linux_install_secure_receiver_service.sh
```

5. From dashboard, click **Trigger Agents**.

This new flow uses:
- Windows: `windows_secure_receiver.ps1` + `windows_run_backup_script_and_push.ps1`
- Linux: `linux_secure_receiver.sh` + `linux_secure_receiver_worker.sh`

## 1) Runtime requirement

- Linux: `bash` + `curl` (+ `rclone` for backup jobs)
- Windows: PowerShell + `Invoke-RestMethod` (+ `rclone` for backup jobs)
- Python scripts are optional, not required.
- Linux secure hub-trigger mode: add `socat` + `openssl` + `jq`

## 2) Script options

- `push_by_exit_code.py`:
  - Use when you run backup command in wrapper script and have exit code.
- `report_rclone_log.py`:
  - Use when backup job already exists and you only parse log after job completes.
- `linux_rclone_backup_and_push.sh`:
  - No Python. Run rclone and push by exit code.
- `linux_push_from_log.sh`:
  - No Python. Parse log and push `last_error_line` + `last_10_log_lines`.
- `windows_rclone_backup_and_push.ps1`:
  - No Python. Run rclone and push by exit code.
- `windows_push_from_log.ps1`:
  - No Python. Parse log and push `last_error_line` + `last_10_log_lines`.
- `report_directadmin_log.py`:
  - Parse DirectAdmin backup log and push result to hub.
- `windows_icewarp_backup_and_push.ps1`:
  - Run IceWarp backup by rclone and push final status to hub.
  - Follows IceWarp flow (mail + archive + cleanup) and pushes:
    - `success` if both mail/archive exit 0
    - `warning` if one of mail/archive fails
    - `failed` if script-level error
  - Includes `error_reason`, `last_error_line`, `last_10_log_lines` in payload for troubleshooting.
- `windows_secure_receiver.ps1`:
  - Receive signed hub trigger requests (HMAC + timestamp + nonce + IP allowlist).
- `windows_run_backup_script_and_push.ps1`:
  - Run any existing Windows backup script (example `backup-icewarp.ps1`) and push final status/log tail to hub.
- `windows_secure_receiver_runner.ps1`:
  - Keep receiver running and auto-restart if it exits/crashes.
- `windows_install_secure_receiver_task.ps1`:
  - Install secure receiver as startup scheduled task (`SYSTEM`), with URLACL + firewall rule.
- `windows_remove_secure_receiver_task.ps1`:
  - Remove startup task (+ optional URLACL/firewall cleanup).
- `linux_secure_receiver.sh`:
  - Receive signed hub trigger requests on Linux (HMAC + timestamp + nonce + IP allowlist).
- `linux_secure_receiver_worker.sh`:
  - Internal request handler used by `linux_secure_receiver.sh`.
- `linux_secure_receiver_runner.sh`:
  - Keep Linux receiver running and auto-restart.
- `linux_install_secure_receiver_service.sh`:
  - Install Linux secure receiver as systemd service.
- `linux_remove_secure_receiver_service.sh`:
  - Remove Linux secure receiver systemd service.

## 3) Linux setup (cron) - Legacy/Optional

1. Copy project/scripts to remote node (or clone repo).
2. Edit variables in:
   - `linux_rclone_backup_and_push.sh` (run backup + push)
   - or `linux_push_from_log.sh` (parse log + push)
3. Make executable:

```bash
chmod +x scripts/agent/linux_rclone_backup_and_push.sh
chmod +x scripts/agent/linux_push_from_log.sh
```

4. Add cron entry (`crontab -e`):

```cron
# Run backup and push status every day at 01:00
0 1 * * * /opt/backup-dashboard/scripts/agent/linux_rclone_backup_and_push.sh >> /var/log/backup-agent.log 2>&1

# Parse log and push every day at 01:15 (if job already scheduled elsewhere)
15 1 * * * /opt/backup-dashboard/scripts/agent/linux_push_from_log.sh >> /var/log/backup-agent.log 2>&1
```

## 4) Windows setup (Task Scheduler) - Legacy/Optional

1. Keep scripts in e.g. `C:\backup-dashboard\scripts\agent\`.
2. Edit values in:
   - `windows_rclone_backup_and_push.ps1`
   - or `windows_push_from_log.ps1`
3. Create task (PowerShell as Administrator):

```powershell
# Daily 01:00, run backup + push
schtasks /Create /TN "BackupHub-Rclone-Nightly" /SC DAILY /ST 01:00 `
  /TR "powershell -ExecutionPolicy Bypass -File C:\backup-dashboard\scripts\agent\windows_rclone_backup_and_push.ps1" `
  /RU SYSTEM

# Daily 01:15, parse log + push
schtasks /Create /TN "BackupHub-Rclone-ParseLog" /SC DAILY /ST 01:15 `
  /TR "powershell -ExecutionPolicy Bypass -File C:\backup-dashboard\scripts\agent\windows_push_from_log.ps1" `
  /RU SYSTEM

# Daily 23:30, IceWarp backup + push
schtasks /Create /TN "BackupHub-IceWarp-Nightly" /SC DAILY /ST 23:30 `
  /TR "powershell -ExecutionPolicy Bypass -File C:\backup-dashboard\scripts\agent\windows_icewarp_backup_and_push.ps1" `
  /RU SYSTEM
```

## 5) Quick manual test

```bash
HUB_URL=http://10.10.10.10:8000 \
INGEST_TOKEN=change_me \
NODE_NAME=test-node \
JOB_NAME=test-job \
RCLONE_CMD="true" \
bash /opt/backup-dashboard/scripts/agent/linux_rclone_backup_and_push.sh
```

DirectAdmin log test:

```bash
python scripts/agent/report_directadmin_log.py \
  --hub http://10.10.10.10:8000 \
  --token change_me \
  --node da-node-01 \
  --job directadmin-nightly \
  --log /var/log/directadmin/backup.log \
  --dry-run
```

## 6) Secure Hub-Triggered Mode (Windows)

If you want hub to trigger agent proactively (instead of waiting for cron/task):

1. Install receiver as auto-start task (recommended, run PowerShell as Administrator):

```powershell
powershell -ExecutionPolicy Bypass -File C:\backup-dashboard\scripts\agent\windows_install_secure_receiver_task.ps1 `
  -TaskName "BackupHub-SecureReceiver" `
  -ListenPrefix "http://+:9189/" `
  -RoutePath "/collect" `
  -SharedSecret "<shared-secret>" `
  -AllowedHubIPs "103.238.213.14" `
  -DefaultHubUrl "https://103.238.213.14" `
  -DefaultIngestToken "<INGEST_API_TOKEN>"
```

2. (Optional) run receiver manually for quick debug:

```powershell
powershell -ExecutionPolicy Bypass -File C:\backup-dashboard\scripts\agent\windows_secure_receiver.ps1 `
  -ListenPrefix "http://+:9189/" `
  -RoutePath "/collect" `
  -SharedSecret "<shared-secret>" `
  -AllowedHubIPs "103.238.213.14"
```

3. Configure hub `.env`:
- `AGENT_TRIGGER_ENABLED=true`
- `AGENT_NODES_JSON=[{"name":"win-bk01","url":"http://win-bk01:9189/collect","shared_secret":"<shared-secret>","verify_ssl":false,"action":"rclone_log_push","payload":{"job_name":"nightly-share","log_path":"C:\\Logs\\rclone-nightly-share.log"}}]`

Example to trigger an existing backup script file on Windows node:
- `AGENT_NODES_JSON=[{"name":"win-icewarp-01","url":"http://win-icewarp-01:9189/collect","shared_secret":"<shared-secret>","verify_ssl":false,"action":"backup_script_push","payload":{"job_name":"icewarp-nightly","backup_script_path":"D:\\scripts\\backup-icewarp.ps1","main_log_path":"D:\\scripts\\backup-icewarp.log"}}]`

4. Trigger from dashboard button `Trigger Agents` or API:
- `POST /api/agents/trigger-all`
- `POST /api/agents/trigger/{name}`

5. Remove task when needed:

```powershell
powershell -ExecutionPolicy Bypass -File C:\backup-dashboard\scripts\agent\windows_remove_secure_receiver_task.ps1 `
  -TaskName "BackupHub-SecureReceiver" `
  -ListenPrefix "http://+:9189/"
```

Security notes:
- Use strong random `shared_secret` per node (>= 32 chars).
- Keep `RequestTtlSeconds` low (default 120s).
- Restrict node firewall to Hub IP only.
- Prefer HTTPS / private network between hub and node.
- Keep `AllowInsecureHubUrl=false` unless you are in isolated private lab network.
- Restrict `AllowedScriptRoots` and `AllowedLogRoots` to minimum needed folders.

## 7) Secure Hub-Triggered Mode (Linux)

Install dependencies:

```bash
sudo apt update
sudo apt install -y socat openssl jq curl
```

Make scripts executable:

```bash
chmod +x scripts/agent/linux_secure_receiver.sh
chmod +x scripts/agent/linux_secure_receiver_worker.sh
chmod +x scripts/agent/linux_secure_receiver_runner.sh
chmod +x scripts/agent/linux_install_secure_receiver_service.sh
chmod +x scripts/agent/linux_remove_secure_receiver_service.sh
```

Install as systemd service:

```bash
sudo SERVICE_NAME=backuphub-secure-receiver \
  ENV_FILE=/opt/backup-dashboard/scripts/agent/linux_secure_receiver.env \
  RUNNER_SCRIPT=/opt/backup-dashboard/scripts/agent/linux_secure_receiver_runner.sh \
  /opt/backup-dashboard/scripts/agent/linux_install_secure_receiver_service.sh
```

Edit env file:
- `SHARED_SECRET=<same as AGENT_NODES_JSON.shared_secret, >= 32 chars>`
- `ALLOWED_HUB_IPS=<hub-ip>`
- `DEFAULT_HUB_URL=https://<hub-domain-or-ip>`
- `DEFAULT_INGEST_TOKEN=<INGEST_API_TOKEN>`
- `DEFAULT_NODE_NAME=<linux-node-name>`
- `DEFAULT_RCLONE_LOG_PATH=/var/log/rclone_da_backup.log`
- `ALLOWED_ACTIONS=health,rclone_log_push`
- `ALLOWED_LOG_ROOTS=/var/log,/opt/backup-logs`

Restart service after edits:

```bash
sudo systemctl restart backuphub-secure-receiver
sudo systemctl status backuphub-secure-receiver --no-pager
```

Hub `.env` sample:

```env
AGENT_TRIGGER_ENABLED=true
AGENT_NODES_JSON=[{"name":"linux-bk01","url":"http://10.10.10.21:9189/collect","shared_secret":"<shared-secret>","verify_ssl":false,"action":"rclone_log_push","payload":{"job_name":"rclone-da","log_path":"/var/log/rclone_da_backup.log"}}]
```

Trigger from hub:
- Dashboard button `Trigger Agents`
- `POST /api/agents/trigger-all`
- `POST /api/agents/trigger/{name}`
