param(
  [string]$HubUrl = "http://10.10.10.10:8000",
  [string]$IngestToken = "change_me",
  [string]$NodeName = "win-bk01",
  [string]$JobName = "nightly-share",
  [string]$LogPath = "C:\Logs\rclone-nightly-share.log"
)

$ErrorActionPreference = "Stop"

if (!(Test-Path $LogPath)) {
  $status = "failed"
  $message = "log file not found: $LogPath"
  $lastErrorLine = $message
  $errorsLine = ""
  $last10 = @()
}
else {
  $lines = Get-Content -Path $LogPath -Tail 1200
  $lastErrorLine = [string]($lines | Where-Object { $_ -match "(?i)\bERROR\b|failed to|fatal" } | Select-Object -Last 1)
  $errorsLine = [string]($lines | Where-Object { $_ -match "(?i)Errors:\s*[0-9]+" } | Select-Object -Last 1)
  $last10 = @($lines | Select-Object -Last 10 | ForEach-Object { [string]$_ })

  $status = "unknown"
  $message = "cannot infer final status from log"
  if ($errorsLine -match "(?i)Errors:\s*0") {
    $status = "success"
    $message = "errors: 0"
  }
  elseif ($lastErrorLine) {
    $status = "failed"
    $message = $lastErrorLine
  }
  elseif (($lines | Where-Object { $_ -match "(?i)Transferred:|Elapsed time:" } | Select-Object -Last 1)) {
    $status = "success"
    $message = "transfer summary detected"
  }
}

$payload = @{
  items = @(
    @{
      node = $NodeName
      source = "rclone"
      job_name = $JobName
      status = $status
      message = $message
      ended_at = (Get-Date).ToUniversalTime().ToString("o")
      raw_payload = @{
        log_path = $LogPath
        errors_line = $errorsLine
        last_error_line = $lastErrorLine
        last_10_log_lines = $last10
      }
    }
  )
}

$json = $payload | ConvertTo-Json -Depth 10 -Compress
$bodyBytes = [System.Text.Encoding]::UTF8.GetBytes($json)

Invoke-RestMethod -Method Post `
  -Uri "$HubUrl/api/ingest/status" `
  -Headers @{ "X-Ingest-Token" = $IngestToken } `
  -ContentType "application/json; charset=utf-8" `
  -Body $bodyBytes
