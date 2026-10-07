param(
  [string]$TaskName = "BackupHub-SecureReceiver",
  [string]$RunnerScriptPath = "$PSScriptRoot\windows_secure_receiver_runner.ps1",
  [string]$ReceiverScriptPath = "$PSScriptRoot\windows_secure_receiver.ps1",
  [string]$ListenPrefix = "http://+:9189/",
  [string]$RoutePath = "/collect",
  [Parameter(Mandatory = $true)]
  [string]$SharedSecret,
  [string]$AllowedHubIPs = "103.238.214.35,127.0.0.1",
  [int]$RequestTtlSeconds = 120,
  [int]$MaxBodyBytes = 1048576,
  [string]$AllowedActions = "health,rclone_log_push,icewarp_backup_push,backup_script_push",
  [string]$AllowedScriptRoots = "D:\scripts",
  [string]$AllowedLogRoots = "D:\scripts,C:\Logs",
  [bool]$AllowInsecureHubUrl = $false,
  [string]$RclonePushScript = "D:\scripts\windows_push_from_log.ps1",
  [string]$IcewarpScript = "D:\scripts\windows_icewarp_backup_and_push.ps1",
  [string]$DefaultHubUrl = "https://103.238.214.35",
  [string]$DefaultIngestToken = "change_me_ingest",
  [string]$DefaultNodeName = "$env:COMPUTERNAME",
  [string]$DefaultRcloneJobName = "nightly-share",
  [string]$DefaultRcloneLogPath = "C:\Logs\rclone-nightly-share.log",
  [string]$RunnerLogPath = "C:\ProgramData\BackupHub\secure-receiver-runner.log",
  [int]$RestartDelaySeconds = 5,
  [bool]$SetupUrlAcl = $true,
  [bool]$SetupFirewall = $true,
  [string]$FirewallRuleName = "BackupHub Secure Receiver 9189"
)

$ErrorActionPreference = "Stop"

function Quote-Arg([string]$value) {
  if ($null -eq $value) {
    return '""'
  }
  return '"' + ($value -replace '"', '`"') + '"'
}

function Get-PortFromPrefix([string]$prefix) {
  if ($prefix -match ":(\d{1,5})/") {
    return [int]$Matches[1]
  }
  return 9189
}

if ($SharedSecret.Length -lt 32) {
  throw "SharedSecret too short. Use at least 32 chars."
}

$adminPrincipal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $adminPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
  throw "Run PowerShell as Administrator to install scheduled task, URLACL, and firewall rule."
}

if (!(Test-Path $RunnerScriptPath)) {
  throw "Runner script not found: $RunnerScriptPath"
}
if (!(Test-Path $ReceiverScriptPath)) {
  throw "Receiver script not found: $ReceiverScriptPath"
}
if (!(Test-Path $RclonePushScript)) {
  Write-Warning "Rclone push script not found now: $RclonePushScript"
}
if (!(Test-Path $IcewarpScript)) {
  Write-Warning "Icewarp script not found now: $IcewarpScript"
}

$runnerResolved = (Resolve-Path $RunnerScriptPath).Path
$receiverResolved = (Resolve-Path $ReceiverScriptPath).Path

if ($SetupUrlAcl) {
  & netsh http delete urlacl url=$ListenPrefix | Out-Null
  & netsh http add urlacl url=$ListenPrefix user="NT AUTHORITY\SYSTEM" | Out-Null
}

if ($SetupFirewall) {
  $port = Get-PortFromPrefix $ListenPrefix
  $remoteAddresses = @($AllowedHubIPs.Split(",") | ForEach-Object { $_.Trim() } | Where-Object { $_ }) -join ","
  if (Get-NetFirewallRule -DisplayName $FirewallRuleName -ErrorAction SilentlyContinue) {
    Remove-NetFirewallRule -DisplayName $FirewallRuleName | Out-Null
  }
  New-NetFirewallRule `
    -DisplayName $FirewallRuleName `
    -Direction Inbound `
    -Action Allow `
    -Protocol TCP `
    -LocalPort $port `
    -RemoteAddress $remoteAddresses `
    -Profile Any | Out-Null
}

$allowInsecureHubUrlValue = if ($AllowInsecureHubUrl) { 1 } else { 0 }

$actionArgs = @(
  "-NoProfile",
  "-ExecutionPolicy", "Bypass",
  "-File", (Quote-Arg $runnerResolved),
  "-ReceiverScriptPath", (Quote-Arg $receiverResolved),
  "-ListenPrefix", (Quote-Arg $ListenPrefix),
  "-RoutePath", (Quote-Arg $RoutePath),
  "-SharedSecret", (Quote-Arg $SharedSecret),
  "-AllowedHubIPs", (Quote-Arg $AllowedHubIPs),
  "-RequestTtlSeconds", $RequestTtlSeconds,
  "-MaxBodyBytes", $MaxBodyBytes,
  "-AllowedActions", (Quote-Arg $AllowedActions),
  "-AllowedScriptRoots", (Quote-Arg $AllowedScriptRoots),
  "-AllowedLogRoots", (Quote-Arg $AllowedLogRoots),
  "-AllowInsecureHubUrl", $allowInsecureHubUrlValue,
  "-RclonePushScript", (Quote-Arg $RclonePushScript),
  "-IcewarpScript", (Quote-Arg $IcewarpScript),
  "-DefaultHubUrl", (Quote-Arg $DefaultHubUrl),
  "-DefaultIngestToken", (Quote-Arg $DefaultIngestToken),
  "-DefaultNodeName", (Quote-Arg $DefaultNodeName),
  "-DefaultRcloneJobName", (Quote-Arg $DefaultRcloneJobName),
  "-DefaultRcloneLogPath", (Quote-Arg $DefaultRcloneLogPath),
  "-RunnerLogPath", (Quote-Arg $RunnerLogPath),
  "-RestartDelaySeconds", $RestartDelaySeconds
) -join " "

$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $actionArgs
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet `
  -StartWhenAvailable `
  -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries `
  -RestartCount 999 `
  -RestartInterval (New-TimeSpan -Minutes 1) `
  -ExecutionTimeLimit (New-TimeSpan -Seconds 0)
$taskPrincipal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest

Register-ScheduledTask `
  -TaskName $TaskName `
  -Action $action `
  -Trigger $trigger `
  -Settings $settings `
  -Principal $taskPrincipal `
  -Force | Out-Null

Start-ScheduledTask -TaskName $TaskName

Write-Host "Installed task: $TaskName"
Write-Host "ListenPrefix: $ListenPrefix"
Write-Host "RoutePath: $RoutePath"
Write-Host "AllowedHubIPs: $AllowedHubIPs"
Write-Host "RunnerLogPath: $RunnerLogPath"
