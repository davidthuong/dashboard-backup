param(
  [string]$TaskName = "BackupHub-SecureReceiver",
  [string]$ListenPrefix = "http://+:9189/",
  [bool]$RemoveUrlAcl = $true,
  [bool]$RemoveFirewallRule = $true,
  [string]$FirewallRuleName = "BackupHub Secure Receiver 9189"
)

$ErrorActionPreference = "Stop"

$adminPrincipal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $adminPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
  throw "Run PowerShell as Administrator to remove scheduled task, URLACL, and firewall rule."
}

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
  Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
  Write-Host "Removed scheduled task: $TaskName"
}
else {
  Write-Host "Scheduled task not found: $TaskName"
}

if ($RemoveUrlAcl) {
  & netsh http delete urlacl url=$ListenPrefix | Out-Null
  Write-Host "Removed URL ACL: $ListenPrefix"
}

if ($RemoveFirewallRule) {
  if (Get-NetFirewallRule -DisplayName $FirewallRuleName -ErrorAction SilentlyContinue) {
    Remove-NetFirewallRule -DisplayName $FirewallRuleName | Out-Null
    Write-Host "Removed firewall rule: $FirewallRuleName"
  }
  else {
    Write-Host "Firewall rule not found: $FirewallRuleName"
  }
}
