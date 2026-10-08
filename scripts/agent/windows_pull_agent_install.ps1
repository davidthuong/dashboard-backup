# BackupHub pull agent installer for Windows (PowerShell 4.0+, Windows Server 2012 R2 and later).
#
# Normally started by the one-line command from the hub ("Add node" on the dashboard, or
# `python -m app.agent_cli enroll`), pasted into PowerShell "Run as Administrator". It:
#   1. checks the hub TLS certificate against the SHA1 in the command; a self-signed hub certificate is
#      pinned by SHA1 in config.json (nothing is added to the Windows certificate stores),
#   2. finds the job log (default D:\scripts\backup-icewarp.log) and enrolls this server on the hub,
#   3. installs agent.ps1 + config.json into C:\Program Files\BackupHub\agent (SYSTEM/Administrators only),
#   4. registers the "BackupHub-Agent" scheduled task (SYSTEM, every minute) and waits for its first poll.
# Only outbound HTTPS to the hub is needed: no listening port, no firewall rule.
#
# Re-running with a new install command for the same name re-enrolls the node (keeps its history).
# Uninstall:  powershell -ExecutionPolicy Bypass -File windows_pull_agent_install.ps1 -Uninstall
#
# Keep this file PowerShell 4.0 compatible and never call `exit`: it runs inside the admin's console.
param(
  [string]$HubUrl = "",
  [string]$CertSha1 = "",
  [string]$EnrollToken = "",
  [string]$Name = "",
  [string]$JobName = "icewarp-nightly",
  [string]$LogPath = "",
  # Under Program Files so that non-admin users cannot pre-create the folder the SYSTEM task runs from.
  [string]$InstallDir = "$env:ProgramFiles\BackupHub\agent",
  [string]$TaskName = "BackupHub-Agent",
  [switch]$AllowInsecureHttp,
  [switch]$NoScheduledTask,
  [switch]$Uninstall
)

$ErrorActionPreference = "Stop"
$DefaultLogPath = "D:\scripts\backup-icewarp.log"

function Write-Step([string]$Text) {
  Write-Host "==> $Text" -ForegroundColor Cyan
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

function Test-IsAdmin {
  $principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
  return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

# TLS handshake that only records what the hub presents; trust is decided by the caller.
function Get-HubCertificate([Uri]$Uri) {
  $state = @{ cert = $null; errors = $null }
  $callback = {
    param($src, $certificate, $chain, $sslPolicyErrors)
    $state.cert = New-Object Security.Cryptography.X509Certificates.X509Certificate2($certificate)
    $state.errors = $sslPolicyErrors
    return $true
  }.GetNewClosure()

  $tcp = New-Object Net.Sockets.TcpClient($Uri.Host, $Uri.Port)
  try {
    $ssl = New-Object Net.Security.SslStream($tcp.GetStream(), $false, ([Net.Security.RemoteCertificateValidationCallback]$callback))
    $ssl.AuthenticateAsClient($Uri.Host, $null, [Security.Authentication.SslProtocols]::Tls12, $false)
    $ssl.Dispose()
  }
  finally {
    $tcp.Close()
  }
  return $state
}

# Same rule as agent.ps1: with a pin, only that exact certificate is accepted; without, normal Windows validation.
function Set-HubPin([string]$Pin) {
  if ($Pin) {
    [Net.ServicePointManager]::ServerCertificateValidationCallback = {
      param($src, $certificate, $chain, $sslPolicyErrors)
      return $certificate.GetCertHashString() -eq $Pin
    }.GetNewClosure()
  }
  else {
    [Net.ServicePointManager]::ServerCertificateValidationCallback = $null
  }
}

# GET over a brand-new connection, so the current certificate rule really is applied.
function Invoke-FreshGet([string]$Url) {
  $request = [Net.HttpWebRequest]::Create($Url)
  $request.Method = "GET"
  $request.KeepAlive = $false
  $request.ConnectionGroupName = [guid]::NewGuid().ToString()
  $request.Timeout = 30000
  $response = $request.GetResponse()
  try {
    $reader = New-Object IO.StreamReader($response.GetResponseStream())
    return $reader.ReadToEnd()
  }
  finally {
    $response.Close()
  }
}

function Invoke-HubPost([string]$Path, $Body) {
  $json = ConvertTo-Json -InputObject $Body -Depth 10 -Compress
  return Invoke-RestMethod -Method Post `
    -Uri ($HubUrl + $Path) `
    -ContentType "application/json; charset=utf-8" `
    -Body ([System.Text.Encoding]::UTF8.GetBytes($json)) `
    -TimeoutSec 60
}

function Uninstall-Agent {
  if (!(Test-IsAdmin)) {
    throw "Run PowerShell as Administrator."
  }
  if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Step "Removed scheduled task $TaskName"
  }
  if (Test-Path $InstallDir) {
    Remove-Item -Recurse -Force $InstallDir
    Write-Step "Removed $InstallDir"
  }
  $parent = Split-Path -Parent $InstallDir
  if ((Test-Path $parent) -and !(Get-ChildItem $parent -Force)) {
    Remove-Item -Force $parent
  }
  Write-Host "Agent uninstalled. Delete the node on the hub dashboard (Agents) if it is not coming back."
}

function Install-Agent {
  if ($PSVersionTable.PSVersion.Major -lt 3) {
    throw "PowerShell 3.0 or later is required (found $($PSVersionTable.PSVersion))."
  }
  if (!$HubUrl -or !$EnrollToken) {
    throw "HubUrl and EnrollToken are required. Copy the install command from the hub dashboard (Agents > Add node)."
  }
  $hubUrl = $HubUrl.TrimEnd("/")
  $hubUri = [Uri]$hubUrl
  if ($hubUri.Scheme -ne "https" -and !$AllowInsecureHttp) {
    throw "HubUrl must use https (use -AllowInsecureHttp only for lab tests)."
  }
  if (!$NoScheduledTask -and !(Test-IsAdmin)) {
    throw "Run PowerShell as Administrator (needed for the scheduled task and $InstallDir)."
  }

  [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

  # 1. Hub certificate -------------------------------------------------------------------------
  $pin = ""
  if ($hubUri.Scheme -eq "https") {
    Write-Step "Checking hub certificate ($($hubUri.Host):$($hubUri.Port))"
    $seen = Get-HubCertificate $hubUri
    $thumbprint = $seen.cert.Thumbprint
    if ($CertSha1 -and $thumbprint -ne $CertSha1.ToUpper()) {
      throw "Hub certificate does not match the install command (got $thumbprint, expected $CertSha1). Aborting."
    }
    $selfSigned = $seen.cert.Subject -eq $seen.cert.Issuer
    if ($selfSigned) {
      if (!$CertSha1) {
        throw "Hub certificate is self-signed and no -CertSha1 was given to pin it."
      }
      # Self-signed: trust exactly this certificate, for the agent only.
      $pin = $thumbprint
      Write-Step "Pinned hub certificate $thumbprint (expires $($seen.cert.NotAfter.ToString('yyyy-MM-dd')))"
    }
    elseif ($seen.errors -ne [Net.Security.SslPolicyErrors]::None) {
      # A CA certificate (Let's Encrypt) is renewed every few months: pinning it would break the
      # agent at the next renewal, so Windows itself has to trust the CA.
      throw ("Windows does not trust the hub certificate ($($seen.errors), issuer: $($seen.cert.Issuer)). " +
        "Update the root certificates on this server (Windows Update, or: certutil -generateSSTFromWU roots.sst " +
        "then import roots.sst into Trusted Root Certification Authorities) and run the install command again.")
    }
    else {
      Write-Step "Hub certificate is trusted by Windows (normal validation, no pin)"
    }
  }

  Set-HubPin $pin
  try {
    $null = Invoke-FreshGet "$hubUrl/api/agent/v1/ping"
  }
  catch {
    throw "Cannot reach $hubUrl/api/agent/v1/ping: $($_.Exception.Message)"
  }

  # 2. Jobs + enrollment -----------------------------------------------------------------------
  $agentPath = Join-Path $InstallDir "agent.ps1"
  $configPath = Join-Path $InstallDir "config.json"

  $jobs = @()
  if ($LogPath) {
    $jobs += @{ job_name = $JobName; log_path = $LogPath }
  }
  elseif (Test-Path $configPath) {
    # Reinstall: keep the jobs already configured on this server.
    foreach ($job in @((Get-Content -Raw $configPath | ConvertFrom-Json).jobs)) {
      if ($job -and $job.job_name) {
        $jobs += @{ job_name = [string]$job.job_name; log_path = [string]$job.log_path }
      }
    }
  }
  if ($jobs.Count -eq 0 -and (Test-Path $DefaultLogPath)) {
    $jobs += @{ job_name = $JobName; log_path = $DefaultLogPath }
  }
  if ($jobs.Count -eq 0) {
    Write-Warning "No job log found ($DefaultLogPath missing). The node will only send heartbeats; re-run with -LogPath <file> to add a job."
  }
  foreach ($job in $jobs) {
    Write-Step "Job $($job.job_name): $($job.log_path)"
    if (!(Test-Path -LiteralPath $job.log_path)) {
      Write-Warning "Log file not found yet: $($job.log_path)"
    }
  }

  Write-Step "Downloading agent from $hubUrl"
  $agentCode = Invoke-FreshGet "$hubUrl/agent/agent.ps1"
  $agentVersion = ""
  if ($agentCode -match '(?m)^\$AgentVersion\s*=\s*"([^"]+)"') {
    $agentVersion = $Matches[1]
  }

  $osCaption = ""
  try {
    $osCaption = [string](Get-WmiObject Win32_OperatingSystem).Caption
  }
  catch {
  }

  Write-Step "Enrolling on the hub"
  try {
    $enrolled = Invoke-HubPost "/api/agent/v1/enroll" @{
      enroll_token = $EnrollToken
      name = $Name
      hostname = $env:COMPUTERNAME
      os_info = ("{0} / PowerShell {1}" -f $osCaption.Trim(), $PSVersionTable.PSVersion)
      agent_version = $agentVersion
      jobs = $jobs
    }
  }
  catch {
    throw "Enrollment failed: $(Get-ErrorText $_)"
  }
  Write-Step "Enrolled as '$($enrolled.name)'"

  # 3. Files -----------------------------------------------------------------------------------
  if (!(Test-Path $InstallDir)) {
    New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
  }
  if (Test-IsAdmin) {
    # The agent token lives here: SYSTEM (S-1-5-18) and Administrators (S-1-5-32-544) only.
    & icacls $InstallDir /inheritance:r /grant:r "*S-1-5-18:(OI)(CI)F" "*S-1-5-32-544:(OI)(CI)F" | Out-Null
    if ($LASTEXITCODE -ne 0) {
      throw "icacls failed on $InstallDir"
    }
  }

  $config = @{
    hub_url = $hubUrl
    cert_sha1 = $pin
    name = [string]$enrolled.name
    agent_token = [string]$enrolled.agent_token
    jobs = $jobs
  }
  $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
  [IO.File]::WriteAllText($configPath, (ConvertTo-Json -InputObject $config -Depth 5), $utf8NoBom)
  [IO.File]::WriteAllText($agentPath, $agentCode, $utf8NoBom)
  Write-Step "Installed $agentPath (v$agentVersion) and config.json"

  # 4. Scheduled task + first poll -------------------------------------------------------------
  $statusFile = Join-Path $InstallDir "last-poll.txt"
  if (Test-Path $statusFile) {
    Remove-Item -Force $statusFile
  }

  if ($NoScheduledTask) {
    Write-Step "Running the agent once (no scheduled task)"
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $agentPath
  }
  else {
    $action = New-ScheduledTaskAction -Execute "powershell.exe" `
      -Argument ('-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{0}"' -f $agentPath)
    $everyMinute = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
      -RepetitionInterval (New-TimeSpan -Minutes 1) `
      -RepetitionDuration (New-TimeSpan -Days 3650)
    $atStartup = New-ScheduledTaskTrigger -AtStartup
    $taskSettings = New-ScheduledTaskSettingsSet `
      -MultipleInstances IgnoreNew `
      -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
      -StartWhenAvailable `
      -AllowStartIfOnBatteries `
      -DontStopIfGoingOnBatteries
    $taskPrincipal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger @($everyMinute, $atStartup) `
      -Settings $taskSettings -Principal $taskPrincipal -Force | Out-Null
    Write-Step "Registered scheduled task $TaskName (SYSTEM, every minute)"

    Start-ScheduledTask -TaskName $TaskName
    Write-Step "Waiting for the first poll"
    for ($i = 0; $i -lt 30 -and !(Test-Path $statusFile); $i++) {
      Start-Sleep -Seconds 2
    }
  }

  $firstPoll = ""
  if (Test-Path $statusFile) {
    $firstPoll = [string](Get-Content $statusFile -TotalCount 1)
  }
  Write-Host ""
  if ($firstPoll.StartsWith("ok ")) {
    Write-Host "OK: agent '$($enrolled.name)' is polling $hubUrl ($firstPoll)" -ForegroundColor Green
    Write-Host "The node now shows as online on the hub dashboard (Agents). Use 'Run' there to test a job now."
  }
  else {
    Write-Host "Agent installed but the first poll did not succeed yet: $firstPoll" -ForegroundColor Yellow
    Write-Host "Check: Get-Content '$statusFile' ; Get-Content '$(Join-Path $InstallDir 'agent.log')' -Tail 20"
  }

  if (Get-ScheduledTask -TaskName "BackupHub-SecureReceiver" -ErrorAction SilentlyContinue) {
    Write-Host ""
    Write-Host "Note: the old receiver (task BackupHub-SecureReceiver) is still installed. Once this agent works," -ForegroundColor Yellow
    Write-Host "remove this node from AGENT_NODES_JSON on the hub and run windows_remove_secure_receiver_task.ps1." -ForegroundColor Yellow
  }
}

# ---------------------------------------------------------------------------------------------

try {
  if ($Uninstall) {
    Uninstall-Agent
  }
  else {
    Install-Agent
  }
}
finally {
  # The install command pins the hub certificate for this console; never leave that behind.
  [Net.ServicePointManager]::ServerCertificateValidationCallback = $null
}
