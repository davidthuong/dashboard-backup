param(
  [string]$ListenPrefix = "http://+:9189/",
  [string]$RoutePath = "/collect",
  [string]$SharedSecret = "change_me",
  [string]$AllowedHubIPs = "127.0.0.1,::1",
  [int]$RequestTtlSeconds = 120,
  [int]$MaxBodyBytes = 1048576,
  [string]$AllowedActions = "health,rclone_log_push,icewarp_backup_push,backup_script_push",
  [string]$AllowedScriptRoots = "D:\scripts",
  [string]$AllowedLogRoots = "D:\scripts,C:\Logs",
  [bool]$AllowInsecureHubUrl = $false,
  [string]$RclonePushScript = "D:\scripts\windows_push_from_log.ps1",
  [string]$IcewarpScript = "D:\scripts\windows_icewarp_backup_and_push.ps1",
  [string]$BackupScriptWrapper = "D:\scripts\windows_run_backup_script_and_push.ps1",
  [string]$DefaultHubUrl = "https://103.238.213.14",
  [string]$DefaultIngestToken = "change_me_ingest",
  [string]$DefaultNodeName = "win-bk01",
  [string]$DefaultRcloneJobName = "nightly-share",
  [string]$DefaultRcloneLogPath = "C:\Logs\rclone-nightly-share.log"
)

$ErrorActionPreference = "Stop"
$usedNonces = New-Object 'System.Collections.Generic.Dictionary[string,datetime]'
$allowlist = @($AllowedHubIPs.Split(",") | ForEach-Object { $_.Trim() } | Where-Object { $_ -ne "" })

if ([string]::IsNullOrWhiteSpace($SharedSecret) -or $SharedSecret -eq "change_me" -or $SharedSecret.Length -lt 32) {
  throw "SharedSecret is weak or missing. Use at least 32 random chars."
}

function To-Hex([byte[]]$bytes) {
  return ([BitConverter]::ToString($bytes) -replace "-", "").ToLowerInvariant()
}

function Get-HmacSignature([string]$secret, [string]$message) {
  $hmac = New-Object System.Security.Cryptography.HMACSHA256
  $hmac.Key = [Text.Encoding]::UTF8.GetBytes($secret)
  $hash = $hmac.ComputeHash([Text.Encoding]::UTF8.GetBytes($message))
  $hmac.Dispose()
  return (To-Hex $hash)
}

function ConstantTimeEquals([string]$a, [string]$b) {
  if ($null -eq $a -or $null -eq $b) { return $false }
  if ($a.Length -ne $b.Length) { return $false }
  $diff = 0
  for ($i = 0; $i -lt $a.Length; $i++) {
    $diff = $diff -bor ([int][char]$a[$i] -bxor [int][char]$b[$i])
  }
  return $diff -eq 0
}

function JsonResponse($ctx, [int]$statusCode, $obj) {
  $ctx.Response.StatusCode = $statusCode
  $ctx.Response.ContentType = "application/json; charset=utf-8"
  $json = $obj | ConvertTo-Json -Depth 8
  $bytes = [System.Text.Encoding]::UTF8.GetBytes($json)
  $ctx.Response.OutputStream.Write($bytes, 0, $bytes.Length)
  $ctx.Response.OutputStream.Close()
}

function Normalize-RoutePath([string]$path) {
  if ([string]::IsNullOrWhiteSpace($path) -or $path -eq "/") {
    return "/"
  }
  $trimmed = $path.Trim()
  if (-not $trimmed.StartsWith("/")) {
    $trimmed = "/$trimmed"
  }
  return $trimmed.TrimEnd("/")
}

function Is-IpAllowed([string]$ip) {
  if ($allowlist.Count -eq 0) { return $true }
  return $allowlist -contains $ip
}

function Get-PathList([string]$csv) {
  return @($csv.Split(",") | ForEach-Object { $_.Trim() } | Where-Object { $_ -ne "" })
}

function Is-ActionAllowed([string]$action) {
  $allowed = Get-PathList $AllowedActions
  return $allowed -contains $action
}

function Is-HubUrlAllowed([string]$url) {
  if ([string]::IsNullOrWhiteSpace($url)) { return $false }
  $lower = $url.ToLowerInvariant()
  if ($AllowInsecureHubUrl) {
    return $lower.StartsWith("http://") -or $lower.StartsWith("https://")
  }
  return $lower.StartsWith("https://")
}

function Is-PathUnderRoots([string]$candidate, [string]$rootsCsv) {
  if ([string]::IsNullOrWhiteSpace($candidate)) { return $false }
  $fullCandidate = ""
  try {
    $fullCandidate = [System.IO.Path]::GetFullPath($candidate)
  } catch {
    return $false
  }

  foreach ($root in (Get-PathList $rootsCsv)) {
    $fullRoot = ""
    try {
      $fullRoot = [System.IO.Path]::GetFullPath($root)
    } catch {
      continue
    }
    $rootNorm = $fullRoot.TrimEnd("\")
    if ($fullCandidate.Equals($rootNorm, [System.StringComparison]::OrdinalIgnoreCase)) {
      return $true
    }
    if ($fullCandidate.StartsWith($rootNorm + "\", [System.StringComparison]::OrdinalIgnoreCase)) {
      return $true
    }
  }
  return $false
}

function Is-Replay([string]$nonce, [datetime]$nowUtc) {
  $expired = @()
  foreach ($kv in $usedNonces.GetEnumerator()) {
    if ($nowUtc.Subtract($kv.Value).TotalSeconds -gt $RequestTtlSeconds) {
      $expired += $kv.Key
    }
  }
  foreach ($k in $expired) {
    $usedNonces.Remove($k) | Out-Null
  }
  if ($usedNonces.ContainsKey($nonce)) {
    return $true
  }
  $usedNonces[$nonce] = $nowUtc
  return $false
}

function Run-Action($action, $payload) {
  switch ($action) {
    "rclone_log_push" {
      if (!(Test-Path $RclonePushScript)) {
        throw "Rclone push script not found: $RclonePushScript"
      }
      $hubUrl = [string]($payload.hub_url | ForEach-Object { $_ }) 
      if ([string]::IsNullOrWhiteSpace($hubUrl)) { $hubUrl = $DefaultHubUrl }
      if (-not (Is-HubUrlAllowed $hubUrl)) {
        throw "hub_url must use https (or set AllowInsecureHubUrl=true)"
      }
      $ingestToken = [string]($payload.ingest_token | ForEach-Object { $_ })
      if ([string]::IsNullOrWhiteSpace($ingestToken)) { $ingestToken = $DefaultIngestToken }
      $nodeName = [string]($payload.node_name | ForEach-Object { $_ })
      if ([string]::IsNullOrWhiteSpace($nodeName)) { $nodeName = $DefaultNodeName }
      $jobName = [string]($payload.job_name | ForEach-Object { $_ })
      if ([string]::IsNullOrWhiteSpace($jobName)) { $jobName = $DefaultRcloneJobName }
      $logPath = [string]($payload.log_path | ForEach-Object { $_ })
      if ([string]::IsNullOrWhiteSpace($logPath)) { $logPath = $DefaultRcloneLogPath }
      if (-not (Is-PathUnderRoots $logPath $AllowedLogRoots)) {
        throw "log_path is outside AllowedLogRoots"
      }

      & powershell -NoProfile -ExecutionPolicy Bypass -File $RclonePushScript `
        -HubUrl $hubUrl `
        -IngestToken $ingestToken `
        -NodeName $nodeName `
        -JobName $jobName `
        -LogPath $logPath
      return @{ action = $action; exit_code = $LASTEXITCODE }
    }
    "icewarp_backup_push" {
      if (!(Test-Path $IcewarpScript)) {
        throw "Icewarp script not found: $IcewarpScript"
      }
      & powershell -NoProfile -ExecutionPolicy Bypass -File $IcewarpScript
      return @{ action = $action; exit_code = $LASTEXITCODE }
    }
    "backup_script_push" {
      if (!(Test-Path $BackupScriptWrapper)) {
        throw "Backup script wrapper not found: $BackupScriptWrapper"
      }
      $hubUrl = [string]($payload.hub_url | ForEach-Object { $_ })
      if ([string]::IsNullOrWhiteSpace($hubUrl)) { $hubUrl = $DefaultHubUrl }
      if (-not (Is-HubUrlAllowed $hubUrl)) {
        throw "hub_url must use https (or set AllowInsecureHubUrl=true)"
      }
      $ingestToken = [string]($payload.ingest_token | ForEach-Object { $_ })
      if ([string]::IsNullOrWhiteSpace($ingestToken)) { $ingestToken = $DefaultIngestToken }
      $nodeName = [string]($payload.node_name | ForEach-Object { $_ })
      if ([string]::IsNullOrWhiteSpace($nodeName)) { $nodeName = $DefaultNodeName }
      $jobName = [string]($payload.job_name | ForEach-Object { $_ })
      if ([string]::IsNullOrWhiteSpace($jobName)) { $jobName = "custom-backup-job" }
      $backupScriptPath = [string]($payload.backup_script_path | ForEach-Object { $_ })
      if ([string]::IsNullOrWhiteSpace($backupScriptPath)) { $backupScriptPath = "D:\scripts\backup-icewarp.ps1" }
      $mainLogPath = [string]($payload.main_log_path | ForEach-Object { $_ })
      if ([string]::IsNullOrWhiteSpace($mainLogPath)) { $mainLogPath = "D:\scripts\backup-icewarp.log" }
      if (-not $backupScriptPath.ToLowerInvariant().EndsWith(".ps1")) {
        throw "backup_script_path must be .ps1"
      }
      if (-not (Is-PathUnderRoots $backupScriptPath $AllowedScriptRoots)) {
        throw "backup_script_path is outside AllowedScriptRoots"
      }
      if (-not (Is-PathUnderRoots $mainLogPath $AllowedLogRoots)) {
        throw "main_log_path is outside AllowedLogRoots"
      }

      & powershell -NoProfile -ExecutionPolicy Bypass -File $BackupScriptWrapper `
        -HubUrl $hubUrl `
        -IngestToken $ingestToken `
        -NodeName $nodeName `
        -JobName $jobName `
        -BackupScriptPath $backupScriptPath `
        -MainLogPath $mainLogPath
      return @{ action = $action; exit_code = $LASTEXITCODE }
    }
    "health" {
      return @{ action = "health"; ok = $true; time = (Get-Date).ToUniversalTime().ToString("o") }
    }
    default {
      throw "Unsupported action: $action"
    }
  }
}

$listener = New-Object System.Net.HttpListener
$listener.Prefixes.Add($ListenPrefix)
$listener.Start()
Write-Host "[agent] listening on $ListenPrefix"
$normalizedRoutePath = Normalize-RoutePath $RoutePath

try {
  while ($listener.IsListening) {
    $ctx = $listener.GetContext()
    try {
      if ($ctx.Request.HttpMethod -ne "POST") {
        JsonResponse $ctx 405 @{ ok = $false; error = "Method not allowed" }
        continue
      }

      $requestPath = Normalize-RoutePath $ctx.Request.Url.AbsolutePath
      if ($normalizedRoutePath -ne "/" -and $requestPath -ne $normalizedRoutePath) {
        JsonResponse $ctx 404 @{ ok = $false; error = "Not found"; route = $requestPath }
        continue
      }

      $remoteIp = $ctx.Request.RemoteEndPoint.Address.ToString()
      if (-not (Is-IpAllowed $remoteIp)) {
        JsonResponse $ctx 403 @{ ok = $false; error = "Remote IP not allowed"; remote_ip = $remoteIp }
        continue
      }

      if ($ctx.Request.ContentLength64 -gt $MaxBodyBytes) {
        JsonResponse $ctx 413 @{ ok = $false; error = "Request body too large" }
        continue
      }

      if ([string]::IsNullOrWhiteSpace($ctx.Request.ContentType) -or $ctx.Request.ContentType -notmatch "(?i)application/json") {
        JsonResponse $ctx 415 @{ ok = $false; error = "Unsupported content type" }
        continue
      }

      $sr = New-Object System.IO.StreamReader($ctx.Request.InputStream, [System.Text.Encoding]::UTF8)
      $body = $sr.ReadToEnd()
      $sr.Close()

      $tsRaw = [string]$ctx.Request.Headers["X-Agent-Timestamp"]
      $nonce = [string]$ctx.Request.Headers["X-Agent-Nonce"]
      $sig = [string]$ctx.Request.Headers["X-Agent-Signature"]

      if ([string]::IsNullOrWhiteSpace($tsRaw) -or [string]::IsNullOrWhiteSpace($nonce) -or [string]::IsNullOrWhiteSpace($sig)) {
        JsonResponse $ctx 401 @{ ok = $false; error = "Missing security headers" }
        continue
      }

      $ts = 0
      if (-not [int64]::TryParse($tsRaw, [ref]$ts)) {
        JsonResponse $ctx 401 @{ ok = $false; error = "Invalid timestamp" }
        continue
      }
      if ($nonce.Length -lt 16 -or $nonce.Length -gt 128 -or $nonce -notmatch "^[A-Za-z0-9._-]+$") {
        JsonResponse $ctx 401 @{ ok = $false; error = "Invalid nonce format" }
        continue
      }
      if ($sig -notmatch "^[a-f0-9]{64}$") {
        JsonResponse $ctx 401 @{ ok = $false; error = "Invalid signature format" }
        continue
      }

      $nowTs = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
      if ([math]::Abs($nowTs - $ts) -gt $RequestTtlSeconds) {
        JsonResponse $ctx 401 @{ ok = $false; error = "Request expired" }
        continue
      }

      if (Is-Replay $nonce ([datetime]::UtcNow)) {
        JsonResponse $ctx 401 @{ ok = $false; error = "Replay detected" }
        continue
      }

      $expected = Get-HmacSignature $SharedSecret "$tsRaw.$nonce.$body"
      if (-not (ConstantTimeEquals $expected $sig)) {
        JsonResponse $ctx 401 @{ ok = $false; error = "Invalid signature" }
        continue
      }

      $payload = $body | ConvertFrom-Json
      $action = [string]$payload.action
      $actionPayload = $payload.payload
      if (-not (Is-ActionAllowed $action)) {
        JsonResponse $ctx 400 @{ ok = $false; error = "Action not allowed" }
        continue
      }

      $result = Run-Action $action $actionPayload
      JsonResponse $ctx 200 @{ ok = $true; result = $result }
    }
    catch {
      JsonResponse $ctx 500 @{ ok = $false; error = $_.Exception.Message }
    }
  }
}
finally {
  $listener.Stop()
}
