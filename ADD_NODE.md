# Thêm server (node) mới vào Backup Hub — pull agent

Cài 1 lệnh trên node, node tự đăng ký với hub. Không mở cổng, không sửa `.env`, không restart hub.

```
node (Scheduled Task "BackupHub-Agent", SYSTEM, mỗi phút)
   |  POST https://103.238.214.35/api/agent/v1/poll   (Bearer token riêng của node)
   |  <- "có lệnh chạy không?"  (hub xếp lệnh theo AGENT_AUTO_TRIGGER_TIMES hoặc nút Run / Trigger Agents)
   |
   +-- có lệnh: đọc D:\scripts\backup-icewarp.log --> POST /api/agent/v1/report --> dashboard + cảnh báo
```

- Node chỉ cần **ra được HTTPS (443) tới hub**. Hub không bao giờ kết nối vào node.
- Hub chỉ ra lệnh "chạy ngay"; đọc log nào do `config.json` trên node quyết định.
- Node không poll quá `AGENT_OFFLINE_AFTER_MINUTES` (mặc định 10 phút) → dashboard hiện **offline** + gửi cảnh báo.
- Hub hẹn giờ mà node đang tắt → lệnh vẫn chờ, node bật lại là chạy.
- Script giữ tương thích **PowerShell 4.0** (Windows Server 2012 R2).

---

## 1. Thêm node

1. Dashboard → thẻ **Agents** → **Add node** → nhập tên (vd `promain-mail`; để trống = hostname của node)
   → **Create install command** → **Copy**.

   Hoặc trên hub:
   ```bash
   docker exec backup-dashboard-app python -m app.agent_cli enroll --name promain-mail
   ```
   Lệnh dùng **1 lần**, hết hạn sau 24 giờ (`AGENT_ENROLL_TTL_MINUTES`).

2. Trên node: mở **PowerShell → Run as Administrator** → dán lệnh → Enter.

   Kết quả đúng (dòng cuối màu xanh):
   ```
   ==> Checking hub certificate (103.238.214.35:443)
   ==> Pinned hub certificate <SHA1> (expires ...)
   ==> Job icewarp-nightly: D:\scripts\backup-icewarp.log
   ==> Enrolled as 'promain-mail'
   ==> Registered scheduled task BackupHub-Agent (SYSTEM, every minute)
   OK: agent 'promain-mail' is polling https://103.238.214.35 (ok ... idle)
   ```

3. Dashboard → **Agents**: node `online`. Bấm **Run** → trong ~1 phút cột *Last run* có kết quả và
   *Latest Status* có dòng `[promain-mail] icewarp-nightly`.

Lệnh cài tự làm: kiểm tra chứng chỉ hub đúng SHA1 trong lệnh (sai là dừng), ghim (pin) SHA1 đó vào `config.json`
của agent (không thêm gì vào certificate store của Windows), tìm log, đăng ký node, cài file, tạo Scheduled Task,
chờ lần poll đầu tiên.

**Job**: mặc định `icewarp-nightly` đọc `D:\scripts\backup-icewarp.log` nếu file tồn tại. Log khác:
thêm vào cuối lệnh cài `-LogPath "E:\path\file.log" -JobName "ten-job"`.
Log phải có các dòng mà agent dựa vào (giống script cũ): `START BACKUP` (đầu mỗi lần chạy) và
`BACKUP HOAN TAT THANH CONG` / `BACKUP HOAN TAT NHUNG CO LOI` (kết thúc).

---

## 2. Trên node

| | |
|---|---|
| Thư mục | `C:\Program Files\BackupHub\agent\` (chỉ SYSTEM + Administrators đọc được — có token) |
| `agent.ps1` | agent |
| `config.json` | địa chỉ hub, SHA1 chứng chỉ hub, tên node, token, danh sách job |
| `last-poll.txt` | kết quả lần poll gần nhất (`ok ...` / `error ...`) |
| `agent.log` | chỉ ghi khi đổi trạng thái hoặc khi chạy job |
| Scheduled Task | `BackupHub-Agent` (SYSTEM, mỗi phút + lúc khởi động) |

Kiểm tra:
```powershell
Get-Content "C:\Program Files\BackupHub\agent\last-poll.txt"
Get-Content "C:\Program Files\BackupHub\agent\agent.log" -Tail 20
Get-ScheduledTask -TaskName BackupHub-Agent | Get-ScheduledTaskInfo | Select-Object LastRunTime, LastTaskResult
```

Chạy job ngay trên node (không cần hub ra lệnh):
```powershell
powershell -ExecutionPolicy Bypass -File "C:\Program Files\BackupHub\agent\agent.ps1" -RunNow
```

Đổi / thêm job: sửa `jobs` trong `config.json` (có hiệu lực ở lần chạy tiếp theo), hoặc tạo lệnh cài mới trên hub
và chạy lại với `-LogPath ... -JobName ...`.

Cài lại / cập nhật agent: tạo lệnh cài mới **có nhập đúng tên node** và chạy lại. Node giữ nguyên tên, lịch sử và job;
token cũ hết hiệu lực. Lệnh không có tên không được ghi đè node đã tồn tại (báo `409 ... already in use`).
Dashboard hiện nhãn **update** khi agent trên node cũ hơn bản trên hub.

Gỡ agent (PowerShell Run as Administrator):
```powershell
Unregister-ScheduledTask -TaskName BackupHub-Agent -Confirm:$false
Remove-Item -Recurse -Force "C:\Program Files\BackupHub"
```
rồi bấm **Delete** ở node đó trên dashboard.

---

## 3. Lần đầu bật tính năng trên hub (1 lần)

```bash
ssh root@103.238.214.35
cd /opt/backup-dashboard
git pull
grep -q '^AGENT_HUB_URL=' .env || echo 'AGENT_HUB_URL=https://103.238.214.35' >> .env
docker compose -f docker-compose.prod.yml up -d --build app
docker exec backup-dashboard-app python -m app.agent_cli list
```

`AGENT_HUB_URL` là địa chỉ node dùng để gọi hub (lệnh `agent_cli enroll` cần nó; dashboard để trống thì lấy
địa chỉ trên trình duyệt). SHA1 chứng chỉ hub được app tự đọc từ nginx (`AGENT_TLS_PROBE_ADDR=nginx:443`).

---

## 4. Chuyển node cũ (hub1/hub2/hub3) sang agent mới

Làm từng node, ví dụ hub1:

1. **Add node** với tên **`hub1`** (đúng `node_name` cũ → dashboard vẫn là `[hub1] icewarp-nightly`, giữ lịch sử).
2. Chạy lệnh cài trên hub1.
3. Bấm **Run** cho `hub1` trên dashboard, chờ kết quả.
4. Trên hub: bỏ object `hub1-icewarp` khỏi `AGENT_NODES_JSON` trong `.env`, rồi
   `docker compose -f docker-compose.prod.yml up -d --force-recreate app`.
5. Trên hub1 (Run as Administrator): gỡ receiver cũ + cổng 9189:
   ```powershell
   powershell -ExecutionPolicy Bypass -File D:\scripts\windows_remove_secure_receiver_task.ps1
   ```
6. (Nên làm) Cách cũ đã đưa chứng chỉ hub vào `LocalMachine\Root` (bước `certutil -addstore`); agent mới không cần nó.
   Nếu trên node không còn script nào khác gửi về hub bằng https, bỏ nó đi để chứng chỉ self-signed của hub
   không còn được Windows tin như một CA:
   ```powershell
   certutil -delstore Root <SHA1 chứng chỉ hub>
   ```

Hết node cũ thì có thể đặt `AGENT_TRIGGER_ENABLED=false`.

---

## 5. Sự cố thường gặp

| Triệu chứng | Nguyên nhân | Cách xử lý |
|---|---|---|
| Lệnh cài báo `Hub certificate does not match the install command` | Chứng chỉ hub đã đổi, hoặc có thiết bị chặn/giả TLS | Tạo lệnh cài mới; nếu vẫn sai thì kiểm tra đường mạng |
| `Could not establish trust relationship` ngay khi dán lệnh | Như trên (SHA1 trong lệnh không khớp) | Như trên |
| `Enrollment failed: ... invalid, already used or expired` | Lệnh đã dùng rồi hoặc quá 24 giờ | Tạo lệnh cài mới |
| `Enrollment failed: (409) ... already in use` | Tên (hoặc hostname) đã có node khác, lệnh lại không ghi tên | Tạo lệnh cài có nhập đúng tên đó (cài lại), hoặc chọn tên khác |
| `Cannot reach .../api/agent/v1/ping` | Node không ra được 443 tới hub (firewall/DNS) | `Test-NetConnection 103.238.214.35 -Port 443` |
| `Run PowerShell as Administrator` | Chưa mở PowerShell bằng quyền Admin | Mở lại bằng Run as Administrator |
| `No job log found` | Không có `D:\scripts\backup-icewarp.log` | Chạy lại với `-LogPath` |
| Dashboard: **offline** | Task không chạy, máy tắt, hoặc mất mạng | Xem `last-poll.txt`, `Get-ScheduledTaskInfo` (mục 2) |
| `last-poll.txt`: `error ... 401 Unknown agent token` | Node đã bị Delete trên hub, hoặc tên này đã cài lại ở máy khác | Tạo lệnh cài mới, chạy lại |
| `last-poll.txt`: `error ... 403 Node is disabled` | Node bị Disable trên dashboard | Bấm **Enable** |
| `last-poll.txt`: `Could not establish trust relationship` | Chứng chỉ hub đã tạo lại (SHA1 đổi) | Tạo lệnh cài mới có tên node, chạy lại trên node (ghim chứng chỉ mới) |
| Kết quả `unknown` | Log không có `START BACKUP` / `BACKUP HOAN TAT ...` | Kiểm tra script backup ghi log đúng mẫu |
| Kết quả `running` | Backup chưa xong lúc chạy | Bình thường; lùi giờ `AGENT_AUTO_TRIGGER_TIMES` nếu lần nào cũng gặp |

**Chứng chỉ self-signed hết hạn sau 365 ngày** (`init_ssl.sh`). Tạo lại chứng chỉ → mọi node mất kết nối →
phải chạy lại lệnh cài trên từng node. Dùng domain + Let's Encrypt (DEPLOY_PROD.md, Case B) thì không gặp chuyện này.

---

Node Linux và node kiểu cũ (receiver, hub gọi vào cổng 9189): [ADD_NODE_LEGACY.md](ADD_NODE_LEGACY.md).
