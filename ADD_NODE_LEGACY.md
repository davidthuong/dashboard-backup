# Node kiểu cũ: receiver (hub gọi vào node)

> Node mới dùng **pull agent**: xem [ADD_NODE.md](ADD_NODE.md). Tài liệu này giữ lại cho các node đang chạy
> receiver (hub1/hub2/hub3) cho tới khi chuyển hết sang agent mới (ADD_NODE.md, mục "Chuyển node cũ").

Luồng hoạt động:

```
Hub (103.238.214.35)  --POST ký HMAC-->  node:9189/collect   (receiver chạy bằng Scheduled Task)
                                              |
                                              v  đọc D:\scripts\backup-icewarp.log
Hub /api/ingest/status  <--POST https + X-Ingest-Token--  node
```

Hub gọi node: theo giờ trong `AGENT_AUTO_TRIGGER_TIMES`, hoặc khi bấm nút **Trigger Agents**.
Node gửi kết quả ngược về hub bằng `windows_push_from_log.ps1`.

Quy ước tên đang dùng: node `hubN-icewarp` trong `AGENT_NODES_JSON`, `node_name` = `hubN`, dashboard hiện `[hubN] icewarp-nightly`.

Các node hiện tại là Windows Server 2012 R2 / **PowerShell 4.0** → script Windows phải giữ tương thích PS 4.0.

---

## A. Chuẩn bị trên hub

```bash
ssh root@103.238.214.35
cd /opt/backup-dashboard
```

1. Tạo shared secret riêng cho node mới (48 ký tự):
   ```bash
   openssl rand -hex 24
   ```
2. Lấy ingest token (node dùng để gửi kết quả về):
   ```bash
   grep '^INGEST_API_TOKEN=' .env
   ```
3. Lấy chứng chỉ của hub (self-signed theo IP, node phải tin chứng chỉ này):
   ```bash
   openssl x509 -noout -subject -enddate -in deploy/letsencrypt/live/*/fullchain.pem
   cat deploy/letsencrypt/live/*/fullchain.pem
   ```
   `subject` phải là `CN = 103.238.214.35` (đúng địa chỉ node dùng trong `-HubUrl`), `notAfter` chưa qua.
   Copy toàn bộ nội dung (gồm dòng `BEGIN/END CERTIFICATE`) sang node, lưu thành `D:\scripts\hub.cer`.

---

## B. Trên node Windows mới (PowerShell **Run as Administrator**)

### 1. Copy script vào `D:\scripts`

Từ repo `scripts/agent/`:
- `windows_secure_receiver.ps1`
- `windows_secure_receiver_runner.ps1`
- `windows_install_secure_receiver_task.ps1`
- `windows_remove_secure_receiver_task.ps1`
- `windows_push_from_log.ps1`
- `windows_run_backup_script_and_push.ps1`

Script backup IceWarp (`D:\scripts\backup-icewarp.ps1`) và task chạy backup hằng đêm **không nằm trong repo** → copy từ một node cũ (hub1/hub2/hub3) và tạo task giống node cũ.
Log `D:\scripts\backup-icewarp.log` phải có các dòng mà `windows_push_from_log.ps1` dựa vào:
- `START BACKUP` (đầu mỗi lần chạy)
- `BACKUP HOAN TAT THANH CONG` hoặc `BACKUP HOAN TAT NHUNG CO LOI` (kết thúc)

### 2. Tin chứng chỉ của hub

```powershell
certutil -addstore -f Root D:\scripts\hub.cer
```

### 3. Thử gửi kết quả từ node về hub (chưa cần receiver)

Bước này kiểm tra chứng chỉ, TLS 1.2 và ingest token trước khi đụng tới hub:

```powershell
powershell -ExecutionPolicy Bypass -File D:\scripts\windows_push_from_log.ps1 `
  -HubUrl "https://103.238.214.35" `
  -IngestToken "<INGEST_API_TOKEN>" `
  -NodeName "hub4" `
  -JobName "icewarp-nightly" `
  -LogPath "D:\scripts\backup-icewarp.log"
```

Đúng: in ra bảng `ok  created  alert_ok` với giá trị `True  1  True`. Sai: xem mục **E**.

### 4. Cài receiver (Scheduled Task chạy bằng SYSTEM, tự khởi động cùng Windows)

```powershell
powershell -ExecutionPolicy Bypass -File D:\scripts\windows_install_secure_receiver_task.ps1 `
  -SharedSecret "<SECRET_O_BUOC_A1>" `
  -AllowedHubIPs "103.238.214.35" `
  -DefaultHubUrl "https://103.238.214.35" `
  -DefaultIngestToken "<INGEST_API_TOKEN>" `
  -DefaultNodeName "hub4"
```

Script tự tạo URLACL + Windows Firewall rule cho cổng 9189 (chỉ cho IP hub).
Nếu nhà cung cấp VPS/cloud có firewall riêng → mở thêm TCP **9189** từ `103.238.214.35`.

### 5. Kiểm tra trên node

```powershell
Get-ScheduledTask -TaskName BackupHub-SecureReceiver | Select-Object TaskName, State
Get-Content C:\ProgramData\BackupHub\secure-receiver-runner.log -Tail 20
netstat -ano | findstr :9189
```

Task `Running`, cổng 9189 `LISTENING`, log không có dòng `crashed` lặp lại.

Đồng hồ node phải lệch hub < 120 giây (nên bật NTP): `w32tm /resync`.

---

## C. Khai báo node trên hub

1. Backup `.env` rồi sửa:
   ```bash
   cd /opt/backup-dashboard
   cp .env .env.bak-$(date +%F)
   nano .env
   ```
2. Trong dòng `AGENT_NODES_JSON=[...]`, thêm object mới **trước dấu `]` cuối**, cách object trước bằng dấu `,`:
   ```json
   {"name":"hub4-icewarp","url":"http://<IP_NODE>:9189/collect","shared_secret":"<SECRET_O_BUOC_A1>","verify_ssl":false,"action":"rclone_log_push","payload":{"node_name":"hub4","job_name":"icewarp-nightly","log_path":"D:\\scripts\\backup-icewarp.log"}}
   ```
   (`\\` trong `log_path` là bắt buộc vì đây là JSON.)
3. Kiểm tra JSON hợp lệ trước khi áp dụng:
   ```bash
   python3 -c "import json; v=[l.split('=',1)[1].strip().strip(\"'\") for l in open('.env') if l.startswith('AGENT_NODES_JSON=')][0]; print(len(json.loads(v)), 'nodes, JSON OK')"
   ```
4. Tạo lại container app để nhận `.env` mới (không cần build lại):
   ```bash
   docker compose -f docker-compose.prod.yml up -d --force-recreate app
   ```
5. Kiểm tra:
   ```bash
   # node mới có trong danh sách? (secret được che)
   docker exec backup-dashboard-app python -m app.agent_cli list

   # hub -> node (không chạy job)
   docker exec backup-dashboard-app python -m app.agent_cli health hub4-icewarp

   # chạy thật: node đọc log và gửi kết quả về hub
   docker exec backup-dashboard-app python -m app.agent_cli trigger hub4-icewarp

   # hub có nhận được không
   docker logs --since 10m backup-dashboard-nginx 2>&1 | grep ingest
   ```
   Đúng: `health` và `trigger` đều `[OK  ]`, `exit_code=0`; nginx có `POST /api/ingest/status ... 200`; dashboard có dòng `[hub4] icewarp-nightly`.

Node mới tự được gọi theo `AGENT_AUTO_TRIGGER_TIMES`, không cần cấu hình thêm.

---

## D. Node Linux

Receiver Linux: xem [scripts/agent/README.md](scripts/agent/README.md) mục 7 (cài `socat openssl jq curl`, service systemd, file env).
Phía hub làm giống mục **C** (url `http://<IP>:9189/collect`, `log_path` dạng `/var/log/...`).

---

## E. Sự cố thường gặp

| Triệu chứng | Nguyên nhân | Cách xử lý |
|---|---|---|
| `health` → `HTTP 0` + `ConnectError` / timeout | Receiver chưa chạy, sai IP/cổng, firewall (Windows hoặc nhà cung cấp) chặn | Mục B5; mở TCP 9189 cho `103.238.214.35` |
| `HTTP 403 Remote IP not allowed` | IP hub không nằm trong `-AllowedHubIPs` | Phản hồi có `remote_ip` = IP hub mà node thấy → cài lại task (B4) với đúng IP đó |
| `HTTP 401 Invalid signature` | Secret trên hub ≠ trên node | Copy lại đúng secret 2 bên, cài lại task |
| `HTTP 401 Request expired` | Đồng hồ hub/node lệch > 120s | `w32tm /resync`, bật NTP |
| `trigger` → `exit_code=1` | Node không gửi được về hub | Chạy lệnh B3 trên node để xem lỗi thật |
| B3 báo `Could not establish trust relationship` | Node chưa tin chứng chỉ hub | B2 |
| B3 báo `underlying connection was closed` | TLS 1.2 không bật | Dùng `windows_push_from_log.ps1` bản mới trong repo |
| B3 báo `(401) Unauthorized` | `-IngestToken` / `-DefaultIngestToken` sai | Lấy lại token (A2), cài lại task |
| Dashboard hiện `unknown` | Log không có `START BACKUP` / `BACKUP HOAN TAT ...` | Kiểm tra script backup IceWarp ghi log đúng mẫu |
| Dashboard hiện `running` | Backup chưa xong lúc trigger | Bình thường; lùi giờ `AGENT_AUTO_TRIGGER_TIMES` nếu lần nào cũng gặp |

---

## F. Lưu ý chứng chỉ self-signed

`init_ssl.sh` tạo chứng chỉ **hạn 365 ngày**. Xem ngày hết hạn:

```bash
openssl x509 -enddate -noout -in /opt/backup-dashboard/deploy/letsencrypt/live/103.238.214.35/fullchain.pem
```

Khi tạo lại chứng chỉ → phải làm lại bước **B2 trên tất cả node**, nếu không mọi node sẽ không gửi được kết quả về hub.
Dùng domain + Let's Encrypt (DEPLOY_PROD.md, Case B) thì không cần bước B2 và tự gia hạn.
