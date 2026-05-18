param(
  [string]$HubUrl = "https://103.238.213.14",
  [string]$IngestToken = "change_me",
  [string]$NodeName = "win-icewarp-01",
  [string]$JobName = "icewarp-nightly-backup",
  [string]$BackupScriptPath = "D:\scripts\backup-icewarp.ps1",
  [string]$MainLogPath = "D:\scripts\backup-icewarp.log"
)

$ErrorActionPreference = "Stop"

function Get-LastLines([string]$Path, [int]$Count = 10) {
  if (!(Test-Path $Path)) { return @() }
  return @((Get-Content -Path $Path -Tail $Count -ErrorAction SilentlyContinue) | ForEach-Object { "$_".TrimEnd() })
}

function Get-LastErrorLine([string]$Path) {
  if (!(Test-Path $Path)) { return "" }
  $lines = Get-Content -Path $Path -Tail 1200 -ErrorAction SilentlyContinue
  if (!$lines) { return "" }
  $lastErr = [string]($lines | Where-Object { $_ -match "(?i)\bERROR\b|failed|fatal|warning" } | Select-Object -Last 1)
  return $lastErr.Trim()
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

  $json = $payload | ConvertTo-Json -Depth 12 -Compress
  $bodyBytes = [System.Text.Encoding]::UTF8.GetBytes($json)
  Invoke-RestMethod -Method Post `
    -Uri "$HubUrl/api/ingest/status" `
    -Headers @{ "X-Ingest-Token" = $IngestToken } `
    -ContentType "application/json; charset=utf-8" `
    -Body $bodyBytes | Out-Null
}

if (!(Test-Path $BackupScriptPath)) {
  throw "Backup script not found: $BackupScriptPath"
}

$runStart = (Get-Date).ToUniversalTime().ToString("o")

set-variable -name LASTEXITCODE -value 0 -scope global
& powershell -NoProfile -ExecutionPolicy Bypass -File $BackupScriptPath
$exitCode = $LASTEXITCODE

$status = "success"
$message = "backup script completed"
if ($exitCode -ne 0) {
  $status = "failed"
  $message = "backup script failed (exit_code=$exitCode)"
}

$lastError = Get-LastErrorLine -Path $MainLogPath
if ($lastError) {
  $message = "$message | $lastError"
}

$rawPayload = @{
  backup_script = $BackupScriptPath
  main_log = $MainLogPath
  started_at = $runStart
  exit_code = $exitCode
  last_error_line = $lastError
  last_10_log_lines = Get-LastLines -Path $MainLogPath -Count 10
}

Push-HubStatus -Status $status -Message $message -RawPayload $rawPayload

if ($exitCode -eq 0) { exit 0 }
exit 1
