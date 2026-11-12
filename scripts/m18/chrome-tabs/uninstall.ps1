<# Removes what install.ps1 wrote: the HKCU key and %LOCALAPPDATA%\m18-tabs (manifest, launcher, log). #>
$ErrorActionPreference = "Stop"
$name = "com.m18pad.tabs"
$reg = "HKCU:\Software\Google\Chrome\NativeMessagingHosts\$name"
if (Test-Path $reg) { Remove-Item -Path $reg -Force; Write-Host "removed $reg" } else { Write-Host "no registry key" }
$dest = Join-Path $env:LOCALAPPDATA "m18-tabs"
if (Test-Path $dest) { Remove-Item -Recurse -Force -Path $dest; Write-Host "removed $dest" } else { Write-Host "no $dest" }
Write-Host "Remove the extension itself at chrome://extensions."
