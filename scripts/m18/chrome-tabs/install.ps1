<#
  Registers the M18 tabs native-messaging host for Chrome (current user only).
    powershell -ExecutionPolicy Bypass -File install.ps1 [-Python <python.exe>] [-DryRun]
  Writes  %LOCALAPPDATA%\m18-tabs\com.m18pad.tabs.json  (host manifest, allowed only for the extension id
  derived from the "key" in extension\manifest.json) and  ...\m18_tabs_host.cmd  (launcher), then sets
  HKCU\Software\Google\Chrome\NativeMessagingHosts\com.m18pad.tabs to the manifest path.
  -DryRun prints what it would write and touches nothing. Undo with uninstall.ps1.
#>
param([string]$Python, [switch]$DryRun)
$ErrorActionPreference = "Stop"
$name = "com.m18pad.tabs"
$here = $PSScriptRoot
$dest = Join-Path $env:LOCALAPPDATA "m18-tabs"

# the daemon's Python: first quoted *.exe in m18ctl.cmd
if (-not $Python) {
  $cmd = Join-Path $env:USERPROFILE "tools\m18\m18ctl.cmd"
  if (Test-Path $cmd) { $m = [regex]::Match((Get-Content $cmd -Raw), '"([^"]+python\.exe)"'); if ($m.Success) { $Python = $m.Groups[1].Value } }
}
if (-not $Python -or -not (Test-Path $Python)) { throw "daemon Python not found; pass -Python <path to python.exe>" }

# extension id = first 16 bytes of SHA-256 of the DER public key, hex digits mapped 0-9a-f -> a-p
$key = (Get-Content (Join-Path $here "extension\manifest.json") -Raw | ConvertFrom-Json).key
$hash = [System.Security.Cryptography.SHA256]::Create().ComputeHash([Convert]::FromBase64String($key))
$id = -join ($hash[0..15] | ForEach-Object { [char](97 + ($_ -shr 4)); [char](97 + ($_ -band 15)) })

$launcher = Join-Path $dest "m18_tabs_host.cmd"
$manifest = Join-Path $dest "$name.json"
$hostPy = Join-Path $here "host\m18_tabs_host.py"
$mf = [ordered]@{ name = $name; description = "M18 Stale Tabs host"; path = $launcher; type = "stdio"; allowed_origins = @("chrome-extension://$id/") }
Write-Host "extension id : $id"
Write-Host "python       : $Python"
Write-Host "host script  : $hostPy"
Write-Host "launcher     : $launcher"
Write-Host "manifest     : $manifest"
Write-Host "registry     : HKCU:\Software\Google\Chrome\NativeMessagingHosts\$name"
if ($DryRun) { Write-Host "(dry run: nothing written)"; return }

New-Item -ItemType Directory -Force -Path $dest | Out-Null
Set-Content -Path $launcher -Encoding ASCII -Value "@echo off`r`n`"$Python`" -u `"$hostPy`" %*"
$mf | ConvertTo-Json | Set-Content -Path $manifest -Encoding UTF8
$reg = "HKCU:\Software\Google\Chrome\NativeMessagingHosts\$name"
New-Item -Path $reg -Force | Out-Null
Set-ItemProperty -Path $reg -Name "(default)" -Value $manifest
Write-Host "Installed. Now load the unpacked extension in Chrome (see references/chrome-tabs.md)."
