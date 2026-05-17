MVP cần gì trước
chỉ xem status hay thêm lọc/tìm kiếm, lịch sử,

Nguồn Veeam
Bạn đang dùng bản nào (VBR version)? Có thể dùng REST API không? - sử dung vbr 12
Cho biết: URL server, kiểu auth (local/AD), và quyền account chỉ đọc.

Nguồn rclone
Task rclone chạy ở đâu (Task Scheduler, systemd, Docker, cron)? Task Scheduler -
Bạn có log file cố định không (đường dẫn + format log)? có

Cách cập nhật dữ liệu
Bạn muốn polling bao lâu/lần (30s, 1m, 5m)? Có cần near-real-time không? trigger tuy chỉnh

Môi trường deploy
Chạy nội bộ Windows hay Linux? Có dùng Docker không? Bao nhiêu người dùng đồng thời?
tùy cachs code. tầm 2-3 người

Bảo mật
Có cần login không (AD/LDAP/local account)? Có phân quyền theo team không? - ko. chỉ cần 1 account admin để xem.

Cảnh báo
Có cần gửi alert qua Email/Telegram/Slack khi job fail không? có 

Stack bạn muốn
Bạn muốn mình làm bằng gì: Node.js, Python, hay .NET? - tùy cách code