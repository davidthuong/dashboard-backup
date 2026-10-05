param(
  [string]$HubUrl = "http://10.10.10.10:8000",
  [string]$IngestToken = "change_me",
  [string]$NodeName = "win-bk01",
  [string]$JobName = "nightly-share",
  [string]$LogPath = "C:\Logs\rclone-nightly-share.log"
)

$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

function Get-LogTimestampUtc([string]$Line) {
  if ([string]::IsNullOrWhiteSpace($Line)) {
    return ""
  }

  $timestamp = ""
  $format = ""
  if ($Line -match '^\[(?<timestamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]') {
    $timestamp = $Matches.timestamp
    $format = "yyyy-MM-dd HH:mm:ss"
  }
  elseif ($Line -match '^(?<timestamp>\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2})') {
    $timestamp = $Matches.timestamp
    $format = "yyyy/MM/dd HH:mm:ss"
  }

  if (!$timestamp) {
    return ""
  }

  try {
    $parsed = [datetime]::ParseExact(
      $timestamp,
      $format,
      [System.Globalization.CultureInfo]::InvariantCulture
    )
    $localTime = [datetime]::SpecifyKind($parsed, [System.DateTimeKind]::Local)
    return $localTime.ToUniversalTime().ToString("o")
  }
  catch {
    return ""
  }
}

$endedAt = (Get-Date).ToUniversalTime().ToString("o")
$statusMarker = ""

if (!(Test-Path $LogPath)) {
  $status = "failed"
  $message = "log file not found: $LogPath"
  $lastErrorLine = $message
  $errorsLine = ""
  $last10 = @()
}
else {
  $lines = @(Get-Content -Path $LogPath -Tail 1200)
  $sessionLines = $lines
  $sessionStartIndex = -1

  for ($i = 0; $i -lt $lines.Count; $i++) {
    if ([string]$lines[$i] -match "(?i)START BACKUP") {
      $sessionStartIndex = $i
    }
  }

  if ($sessionStartIndex -ge 0) {
    $sessionLines = @($lines[$sessionStartIndex..($lines.Count - 1)])
  }

  $lastErrorLine = [string]($sessionLines | Where-Object { $_ -match "(?i)\bERROR\b|failed to|fatal" } | Select-Object -Last 1)
  $scriptErrorLine = [string]($sessionLines | Where-Object { $_ -match '^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\]\s+ERROR:' } | Select-Object -Last 1)
  $errorsLine = [string]($sessionLines | Where-Object { $_ -match "(?i)Errors:\s*[0-9]+" } | Select-Object -Last 1)
  $transferLine = [string]($sessionLines | Where-Object { $_ -match "(?i)Transferred:|Elapsed time:" } | Select-Object -Last 1)
  $finalLine = [string]($sessionLines | Where-Object { $_ -match "(?i)BACKUP HOAN TAT THANH CONG|BACKUP HOAN TAT NHUNG CO LOI" } | Select-Object -Last 1)
  $last10 = @($sessionLines | Select-Object -Last 10 | ForEach-Object { [string]$_ })

  $status = "unknown"
  $message = "cannot infer final status from log"

  if ($finalLine -match "(?i)BACKUP HOAN TAT THANH CONG") {
    $status = "success"
    $message = "backup completed successfully"
    $statusMarker = $finalLine
  }
  elseif ($finalLine -match "(?i)BACKUP HOAN TAT NHUNG CO LOI") {
    $status = "failed"
    $message = $finalLine
    $statusMarker = $finalLine
  }
  elseif ($sessionStartIndex -ge 0 -and $scriptErrorLine) {
    $status = "failed"
    $message = $scriptErrorLine
    $statusMarker = $scriptErrorLine
  }
  elseif ($sessionStartIndex -ge 0) {
    $status = "running"
    $message = "backup started; final status not found yet"
  }
  elseif ($errorsLine -match "(?i)Errors:\s*0") {
    $status = "success"
    $message = "errors: 0"
    $statusMarker = $errorsLine
  }
  elseif ($lastErrorLine) {
    $status = "failed"
    $message = $lastErrorLine
    $statusMarker = $lastErrorLine
  }
  elseif ($transferLine) {
    $status = "success"
    $message = "transfer summary detected"
    $statusMarker = $transferLine
  }

  if ($status -ne "running" -and $statusMarker) {
    $parsedEndedAt = Get-LogTimestampUtc -Line $statusMarker
    if ($parsedEndedAt) {
      $endedAt = $parsedEndedAt
    }
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
      ended_at = $endedAt
      raw_payload = @{
        log_path = $LogPath
        status_marker = $statusMarker
        errors_line = $errorsLine
        last_error_line = $lastErrorLine
        last_10_log_lines = $last10
      }
    }
  )
}

$json = $payload | ConvertTo-Json -Depth 10 -Compress
$bodyBytes = [System.Text.Encoding]::UTF8.GetBytes($json)

try {
  $response = Invoke-RestMethod -Method Post `
    -Uri "$HubUrl/api/ingest/status" `
    -Headers @{ "X-Ingest-Token" = $IngestToken } `
    -ContentType "application/json; charset=utf-8" `
    -Body $bodyBytes
  Write-Output $response
  exit 0
}
catch {
  Write-Error "Failed to push status to $HubUrl`: $($_.Exception.Message)"
  exit 1
}
