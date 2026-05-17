param(
  [string]$HubUrl = "https://103.238.213.14",
  [string]$IngestToken = "change_me",
  [string]$NodeName = "win-icewarp-01",
  [string]$JobName = "icewarp-nightly-backup",
  [string]$Rclone = "D:\scripts\rclone.exe",
  [string]$Remote = "proman:proman-icewarp-backup/icewarp",
  [string]$BwLimit = "40M",
  [string]$SrcMail = "D:\IceWarp\mail",
  [string]$SrcArchive = "D:\IceWarp\archive",
  [string]$LogFile = "D:\scripts\backup-icewarp.log",
  [string]$RcloneLogMail = "D:\scripts\rclone-mail.log",
  [string]$RcloneLogArc = "D:\scripts\rclone-archive.log",
  [string]$RcloneConfig = "C:\Users\Administrator\AppData\Roaming\rclone\rclone.conf"
)

$ErrorActionPreference = "Stop"
$env:RCLONE_CONFIG = $RcloneConfig
$DateTag = Get-Date -Format "yyyy-MM-dd"
$mailExit = 0
$archiveExit = 0
$cleanExit = 0

function Write-Log($msg) {
  $time = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
  $line = "[$time] $msg"
  Write-Host $line
  Add-Content -Path $LogFile -Value $line
}

function Get-RcloneLogSummary([string]$Path) {
  $summary = @{
    transferred_line = ""
    checks_line = ""
    errors_line = ""
    last_error_line = ""
    last_10_log_lines = @()
  }

  if (!(Test-Path $Path)) {
    return $summary
  }

  $lines = Get-Content -Path $Path -Tail 1200
  if (!$lines) {
    return $summary
  }

  $transferred = $lines | Where-Object { $_ -match "Transferred:" } | Select-Object -Last 1
  if ($transferred) { $summary.transferred_line = $transferred.Trim() }

  $checks = $lines | Where-Object { $_ -match "Checks:" } | Select-Object -Last 1
  if ($checks) { $summary.checks_line = $checks.Trim() }

  $errors = $lines | Where-Object { $_ -match "Errors:" } | Select-Object -Last 1
  if ($errors) { $summary.errors_line = $errors.Trim() }

  $lastErr = $lines | Where-Object { $_ -match "(?i)\bERROR\b|failed to|fatal" } | Select-Object -Last 1
  if ($lastErr) { $summary.last_error_line = $lastErr.Trim() }

  $summary.last_10_log_lines = @($lines | Select-Object -Last 10 | ForEach-Object { "$_".TrimEnd() })
  return $summary
}

function Push-HubStatus([string]$Status, [string]$Message, [hashtable]$RawPayload) {
  $payload = @{
    items = @(
      @{
        node = $NodeName
        source = "rclone"
        job_name = $JobName
        status = $Status
        message = $Message
        ended_at = (Get-Date).ToUniversalTime().ToString("o")
        raw_payload = $RawPayload
      }
    )
  }

  try {
    $json = $payload | ConvertTo-Json -Depth 12 -Compress
    $bodyBytes = [System.Text.Encoding]::UTF8.GetBytes($json)
    Invoke-RestMethod -Method Post `
      -Uri "$HubUrl/api/ingest/status" `
      -Headers @{ "X-Ingest-Token" = $IngestToken } `
      -ContentType "application/json; charset=utf-8" `
      -Body $bodyBytes | Out-Null
    Write-Log "PUSH HUB OK: $Status"
  }
  catch {
    Write-Log "WARN: push hub that bai: $($_.Exception.Message)"
  }
}

try {
  Write-Log "START BACKUP $DateTag"
  if ($BwLimit -ne "") {
    Write-Log "BANDWIDTH: $BwLimit (limited mode)"
  }
  else {
    Write-Log "BANDWIDTH: unlimited mode"
  }

  if (!(Test-Path $Rclone)) {
    throw "khong tim thay rclone.exe: $Rclone"
  }
  if (!(Test-Path $SrcMail)) {
    throw "khong tim thay du lieu mail: $SrcMail"
  }
  if (!(Test-Path $SrcArchive)) {
    throw "khong tim thay du lieu archive: $SrcArchive"
  }

  Write-Log "CHECK REMOTE"
  & $Rclone ls (($Remote.Split(":")[0]) + ":") --max-depth 1 >> $LogFile 2>&1
  if ($LASTEXITCODE -ne 0) {
    throw "remote khong hop le"
  }

  Write-Log "BACKUP MAIL START"
  $mailArgs = @(
    "sync", $SrcMail, "$Remote/current/mail",
    "--backup-dir", "$Remote/versions/$DateTag/mail",
    "--size-only",
    "--transfers", "2",
    "--checkers", "4",
    "--multi-thread-streams", "1",
    "--s3-upload-concurrency", "2",
    "--s3-chunk-size", "64M",
    "--retries", "10",
    "--retries-sleep", "5s",
    "--low-level-retries", "3",
    "--log-file=$RcloneLogMail",
    "--log-level", "INFO"
  )
  if ($BwLimit -ne "") { $mailArgs += "--bwlimit"; $mailArgs += $BwLimit }
  & $Rclone @mailArgs
  $mailExit = $LASTEXITCODE
  if ($mailExit -ne 0) {
    Write-Log "WARNING: backup MAIL co loi -- xem: $RcloneLogMail (exit=$mailExit)"
  }
  else {
    Write-Log "BACKUP MAIL DONE"
  }

  Write-Log "BACKUP ARCHIVE START"
  $archiveArgs = @(
    "sync", $SrcArchive, "$Remote/current/archive",
    "--backup-dir", "$Remote/versions/$DateTag/archive",
    "--size-only",
    "--transfers", "2",
    "--checkers", "3",
    "--multi-thread-streams", "1",
    "--s3-upload-concurrency", "2",
    "--s3-chunk-size", "64M",
    "--retries", "10",
    "--retries-sleep", "5s",
    "--low-level-retries", "3",
    "--log-file=$RcloneLogArc",
    "--log-level", "INFO"
  )
  if ($BwLimit -ne "") { $archiveArgs += "--bwlimit"; $archiveArgs += $BwLimit }
  & $Rclone @archiveArgs
  $archiveExit = $LASTEXITCODE
  if ($archiveExit -ne 0) {
    Write-Log "WARNING: backup ARCHIVE co loi -- xem: $RcloneLogArc (exit=$archiveExit)"
  }
  else {
    Write-Log "BACKUP ARCHIVE DONE"
  }

  Write-Log "CLEAN BACKUP >14 NGAY"
  $cutoff = (Get-Date).AddDays(-14).ToString("yyyy-MM-dd")
  & $Rclone lsd "$Remote/versions/" --log-level ERROR | ForEach-Object {
    $dir = $_.Trim().Split()[-1]
    if ($dir -lt $cutoff) {
      Write-Log "Deleting old version: $dir"
      & $Rclone purge "$Remote/versions/$dir" --log-file=$RcloneLogMail --log-level INFO
    }
  }
  $cleanExit = $LASTEXITCODE

  Write-Log "CLEAN LOGS >30 NGAY"
  Get-ChildItem "D:\scripts\" -Filter "*.log" -ErrorAction SilentlyContinue |
    Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-30) } |
    Remove-Item -Force -ErrorAction SilentlyContinue

  Write-Log "BACKUP HOAN TAT"

  $mailSum = Get-RcloneLogSummary -Path $RcloneLogMail
  $arcSum = Get-RcloneLogSummary -Path $RcloneLogArc

  $status = "success"
  $msg = "mail+archive backup done"
  if ($mailExit -ne 0 -or $archiveExit -ne 0) {
    $status = "warning"
    $msg = "backup complete with warning (mail_exit=$mailExit, archive_exit=$archiveExit)"
  }

  $combinedLastError = @($mailSum.last_error_line, $arcSum.last_error_line) | Where-Object { $_ -and $_.Trim() -ne "" } | Select-Object -First 1
  if ($combinedLastError) {
    $msg = "$msg | $combinedLastError"
  }

  $combinedTail = @()
  if ($mailSum.last_10_log_lines) {
    $combinedTail += $mailSum.last_10_log_lines
  }
  if ($arcSum.last_10_log_lines) {
    $combinedTail += $arcSum.last_10_log_lines
  }
  if ($combinedTail.Count -gt 10) {
    $combinedTail = @($combinedTail | Select-Object -Last 10)
  }

  Push-HubStatus -Status $status -Message $msg -RawPayload @{
    date = $DateTag
    remote = $Remote
    bw_limit = $BwLimit
    main_log = $LogFile
    mail_log = $RcloneLogMail
    archive_log = $RcloneLogArc
    mail_exit = $mailExit
    archive_exit = $archiveExit
    clean_exit = $cleanExit
    mail_errors_line = $mailSum.errors_line
    archive_errors_line = $arcSum.errors_line
    last_error_line = $combinedLastError
    last_10_log_lines = $combinedTail
  }

  if ($status -eq "success") {
    exit 0
  }
  exit 2
}
catch {
  $err = $_.Exception.Message
  Write-Log "ERROR: $err"

  $mailSum = Get-RcloneLogSummary -Path $RcloneLogMail
  $arcSum = Get-RcloneLogSummary -Path $RcloneLogArc
  $combinedTail = @()
  if ($mailSum.last_10_log_lines) { $combinedTail += $mailSum.last_10_log_lines }
  if ($arcSum.last_10_log_lines) { $combinedTail += $arcSum.last_10_log_lines }
  if ($combinedTail.Count -gt 10) { $combinedTail = @($combinedTail | Select-Object -Last 10) }

  $combinedLastError = @($mailSum.last_error_line, $arcSum.last_error_line) | Where-Object { $_ -and $_.Trim() -ne "" } | Select-Object -First 1
  $failMsg = $err
  if ($combinedLastError) {
    $failMsg = "$err | $combinedLastError"
  }

  Push-HubStatus -Status "failed" -Message $failMsg -RawPayload @{
    date = $DateTag
    remote = $Remote
    bw_limit = $BwLimit
    main_log = $LogFile
    mail_log = $RcloneLogMail
    archive_log = $RcloneLogArc
    error_reason = $err
    last_error_line = $combinedLastError
    last_10_log_lines = $combinedTail
  }
  exit 1
}
