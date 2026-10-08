# BackupHub pull agent for Windows (PowerShell 4.0+, Windows Server 2012 R2 and later).
#
# Installed by windows_pull_agent_install.ps1 into C:\Program Files\BackupHub\agent and started every
# minute by the "BackupHub-Agent" scheduled task (SYSTEM). Each start:
#   1. polls the hub (heartbeat) over HTTPS with this node's bearer token,
#   2. if the hub queued a run (schedule or "Trigger" button), parses every job log listed in
#      config.json and reports the results.
# The hub never connects to this server, and it can only say "run now": which logs are read is
# decided by config.json on this server.
#
# Manual run (as Administrator):  powershell -ExecutionPolicy Bypass -File agent.ps1 -RunNow
param(
  [string]$ConfigPath = "",
  [switch]$RunNow
)

$AgentVersion = "1.0.0"
$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

if (!$ConfigPath) {
  $ConfigPath = Join-Path $PSScriptRoot "config.json"
}
$AgentDir = Split-Path -Parent $ConfigPath
$LogFile = Join-Path $AgentDir "agent.log"
$StatusFile = Join-Path $AgentDir "last-poll.txt"

function Write-AgentLog([string]$Message) {
  try {
    if ((Test-Path $LogFile) -and (Get-Item $LogFile).Length -gt 1MB) {
      Move-Item -Force $LogFile "$LogFile.1"
    }
    Add-Content -Path $LogFile -Value ("[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message)
  }
  catch {
  }
}

# One line per poll in last-poll.txt; agent.log only gets a line when the state changes.
function Set-PollStatus([string]$State, [string]$Detail) {
  $previous = ""
  if (Test-Path $StatusFile) {
    $previous = [string](Get-Content $StatusFile -TotalCount 1)
  }
  $line = "{0} {1} {2}" -f $State, (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Detail
  Set-Content -Path $StatusFile -Value $line
  if (-not $previous.StartsWith("$State ") -or $State -ne "ok") {
    Write-AgentLog "poll $State $Detail"
  }
}

function Get-ErrorText($ErrorRecord) {
  $text = $ErrorRecord.Exception.Message
  if ($ErrorRecord.ErrorDetails -and $ErrorRecord.ErrorDetails.Message) {
    $text = "$text $($ErrorRecord.ErrorDetails.Message)"
  }
  elseif ($ErrorRecord.Exception.Response) {
    try {
      $reader = New-Object IO.StreamReader($ErrorRecord.Exception.Response.GetResponseStream())
      $body = $reader.ReadToEnd()
      if ($body) {
        $text = "$text $body"
      }
    }
    catch {
    }
  }
  return $text
}

function Invoke-Hub([string]$Path, $Body) {
  $json = ConvertTo-Json -InputObject $Body -Depth 10 -Compress
  return Invoke-RestMethod -Method Post `
    -Uri ($script:Config.hub_url.TrimEnd("/") + $Path) `
    -Headers @{ Authorization = "Bearer " + $script:Config.agent_token } `
    -ContentType "application/json; charset=utf-8" `
    -Body ([System.Text.Encoding]::UTF8.GetBytes($json)) `
    -TimeoutSec 60
}

function Get-LogTimestampUtc([string]$Line) {
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
    $parsed = [datetime]::ParseExact($timestamp, $format, [System.Globalization.CultureInfo]::InvariantCulture)
    return [datetime]::SpecifyKind($parsed, [System.DateTimeKind]::Local).ToUniversalTime().ToString("o")
  }
  catch {
    return ""
  }
}

# Same rules as windows_push_from_log.ps1: the last "START BACKUP" opens the session, then
# "BACKUP HOAN TAT THANH CONG" / "BACKUP HOAN TAT NHUNG CO LOI" decide the result.
function Get-JobResult($Job) {
  $logPath = [string]$Job.log_path
  $endedAt = (Get-Date).ToUniversalTime().ToString("o")
  $statusMarker = ""
  $errorsLine = ""
  $lastErrorLine = ""
  $last10 = @()

  if (!$logPath -or !(Test-Path -LiteralPath $logPath)) {
    $status = "failed"
    $message = "log file not found: $logPath"
    $lastErrorLine = $message
  }
  else {
    $lines = @(Get-Content -LiteralPath $logPath -Tail 1200)
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

  return @{
    source = "rclone"
    job_name = [string]$Job.job_name
    status = $status
    message = $message
    ended_at = $endedAt
    raw_payload = @{
      log_path = $logPath
      status_marker = $statusMarker
      errors_line = $errorsLine
      last_error_line = $lastErrorLine
      last_10_log_lines = $last10
      agent_version = $AgentVersion
    }
  }
}

try {
  $script:Config = Get-Content -Raw -Path $ConfigPath | ConvertFrom-Json
}
catch {
  Write-AgentLog "cannot read config $ConfigPath : $($_.Exception.Message)"
  exit 2
}

# Self-signed hub: accept exactly the certificate pinned at install time (nothing in the Windows
# certificate stores). No pin: the hub has a publicly trusted certificate, normal validation applies.
$certPin = [string]$Config.cert_sha1
if ($certPin) {
  [Net.ServicePointManager]::ServerCertificateValidationCallback = {
    param($src, $certificate, $chain, $sslPolicyErrors)
    return $certificate.GetCertHashString() -eq $certPin
  }.GetNewClosure()
}

$jobs = @()
foreach ($job in @($Config.jobs)) {
  if ($job -and $job.job_name) {
    $jobs += @{ job_name = [string]$job.job_name; log_path = [string]$job.log_path }
  }
}

$osCaption = ""
try {
  $osCaption = [string](Get-WmiObject Win32_OperatingSystem).Caption
}
catch {
}

$nodeInfo = @{
  hostname = $env:COMPUTERNAME
  os_info = ("{0} / PowerShell {1}" -f $osCaption.Trim(), $PSVersionTable.PSVersion)
  agent_version = $AgentVersion
  jobs = $jobs
}

try {
  $poll = Invoke-Hub "/api/agent/v1/poll" $nodeInfo
}
catch {
  Set-PollStatus "error" (Get-ErrorText $_)
  exit 1
}

$runId = $null
if ($poll.run -and $poll.run.id) {
  $runId = [string]$poll.run.id
}
if (!$runId -and !$RunNow) {
  Set-PollStatus "ok" "idle"
  exit 0
}

$items = @()
foreach ($job in $jobs) {
  $items += Get-JobResult $job
}
$summary = (@($items | ForEach-Object { "{0}={1}" -f $_.job_name, $_.status }) -join ", ")
if (!$summary) {
  $summary = "no jobs in config.json"
}

try {
  $null = Invoke-Hub "/api/agent/v1/report" @{ run_id = $runId; items = $items }
  Write-AgentLog "run $runId reported: $summary"
  Set-PollStatus "ok" "reported $summary"
  exit 0
}
catch {
  Write-AgentLog "run $runId report failed: $(Get-ErrorText $_)"
  Set-PollStatus "error" "report failed"
  exit 1
}
