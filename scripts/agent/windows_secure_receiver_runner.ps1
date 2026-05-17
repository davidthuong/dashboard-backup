param(
  [string]$ReceiverScriptPath = "$PSScriptRoot\windows_secure_receiver.ps1",
  [string]$ListenPrefix = "http://+:9189/",
  [string]$RoutePath = "/collect",
  [string]$SharedSecret = "change_me",
  [string]$AllowedHubIPs = "127.0.0.1,::1",
  [int]$RequestTtlSeconds = 120,
  [int]$MaxBodyBytes = 1048576,
  [string]$RclonePushScript = "D:\scripts\windows_push_from_log.ps1",
  [string]$IcewarpScript = "D:\scripts\windows_icewarp_backup_and_push.ps1",
  [string]$DefaultHubUrl = "https://103.238.213.14",
  [string]$DefaultIngestToken = "change_me_ingest",
  [string]$DefaultNodeName = "win-bk01",
  [string]$DefaultRcloneJobName = "nightly-share",
  [string]$DefaultRcloneLogPath = "C:\Logs\rclone-nightly-share.log",
  [int]$RestartDelaySeconds = 5,
  [string]$RunnerLogPath = "C:\ProgramData\BackupHub\secure-receiver-runner.log",
  [bool]$RunForever = $true
)

$ErrorActionPreference = "Stop"

function Write-RunnerLog([string]$message) {
  $time = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
  $line = "[$time] $message"
  Write-Host $line
  Add-Content -Path $RunnerLogPath -Value $line
}

if ([string]::IsNullOrWhiteSpace($SharedSecret) -or $SharedSecret -eq "change_me") {
  throw "SharedSecret is weak or missing. Use a strong random secret."
}

if (!(Test-Path $ReceiverScriptPath)) {
  throw "Receiver script not found: $ReceiverScriptPath"
}

$runnerLogDir = Split-Path -Parent $RunnerLogPath
if ($runnerLogDir -and !(Test-Path $runnerLogDir)) {
  New-Item -ItemType Directory -Path $runnerLogDir -Force | Out-Null
}

while ($true) {
  Write-RunnerLog "Starting secure receiver process..."
  try {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $ReceiverScriptPath `
      -ListenPrefix $ListenPrefix `
      -RoutePath $RoutePath `
      -SharedSecret $SharedSecret `
      -AllowedHubIPs $AllowedHubIPs `
      -RequestTtlSeconds $RequestTtlSeconds `
      -MaxBodyBytes $MaxBodyBytes `
      -RclonePushScript $RclonePushScript `
      -IcewarpScript $IcewarpScript `
      -DefaultHubUrl $DefaultHubUrl `
      -DefaultIngestToken $DefaultIngestToken `
      -DefaultNodeName $DefaultNodeName `
      -DefaultRcloneJobName $DefaultRcloneJobName `
      -DefaultRcloneLogPath $DefaultRcloneLogPath

    $exitCode = $LASTEXITCODE
    if ($null -eq $exitCode) {
      $exitCode = 0
    }
    Write-RunnerLog "Receiver process exited with code $exitCode"
  }
  catch {
    Write-RunnerLog "Receiver process crashed: $($_.Exception.Message)"
  }

  if (-not $RunForever) {
    break
  }

  Write-RunnerLog "Restarting in $RestartDelaySeconds seconds..."
  Start-Sleep -Seconds $RestartDelaySeconds
}
