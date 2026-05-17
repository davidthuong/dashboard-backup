param(
  [string]$HubUrl = "http://10.10.10.10:8000",
  [string]$IngestToken = "change_me",
  [string]$NodeName = "win-bk01",
  [string]$JobName = "nightly-share",
  [string]$RcloneExe = "C:\rclone\rclone.exe",
  [string]$SourcePath = "D:\Data",
  [string]$DestPath = "remote:nightly-share",
  [string]$LogPath = "C:\Logs\rclone-nightly-share.log"
)

$ErrorActionPreference = "Stop"

& $RcloneExe sync $SourcePath $DestPath --log-file $LogPath --log-level INFO
$exitCode = $LASTEXITCODE

$status = if ($exitCode -eq 0) { "success" } else { "failed" }
$message = if ($exitCode -eq 0) { "backup command executed" } else { "backup command failed (exit_code=$exitCode)" }

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
        exit_code = $exitCode
        log_path = $LogPath
      }
    }
  )
}

try {
  $json = $payload | ConvertTo-Json -Depth 10 -Compress
  $bodyBytes = [System.Text.Encoding]::UTF8.GetBytes($json)
  Invoke-RestMethod -Method Post `
    -Uri "$HubUrl/api/ingest/status" `
    -Headers @{ "X-Ingest-Token" = $IngestToken } `
    -ContentType "application/json; charset=utf-8" `
    -Body $bodyBytes | Out-Null
}
catch {
  Write-Host "WARN: push hub failed: $($_.Exception.Message)"
}

exit $exitCode
