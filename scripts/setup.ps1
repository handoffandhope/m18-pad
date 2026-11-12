<#
  Installs / updates the M18 daemon on Windows.
    powershell -ExecutionPolicy Bypass -File setup.ps1 [-InstallDir C:\Users\you\tools\m18] [-NoStartup]
  Safe to re-run: code is updated, your macros.yaml and plugins are kept.
#>
param(
  [string]$InstallDir = "$env:USERPROFILE\tools\m18",
  [switch]$NoStartup
)
$ErrorActionPreference = "Stop"
$src = Join-Path $PSScriptRoot "m18"
Write-Host "Installing M18 daemon to $InstallDir"
New-Item -ItemType Directory -Force -Path $InstallDir, "$InstallDir\plugins", "$InstallDir\icons" | Out-Null

# 1. code (never clobber the user's config or their own plugins)
Copy-Item "$src\m18d.py", "$src\m18ctl.py", "$src\theme_preview.py", "$src\e2e_check.py" $InstallDir -Force
Copy-Item "$src\m18lib" $InstallDir -Recurse -Force
Copy-Item "$src\assets" $InstallDir -Recurse -Force
# aesthetics: <skill>\aesthetics\<name>\theme.py -> $InstallDir\themes\<name>\theme.py (shipped themes are
# updated; themes you add under themes\ with other names are left alone)
Get-ChildItem (Join-Path $PSScriptRoot "..\aesthetics") -Directory -ErrorAction SilentlyContinue | ForEach-Object {
  $t = Join-Path $_.FullName "theme.py"
  if (Test-Path $t) {
    New-Item -ItemType Directory -Force -Path "$InstallDir\themes\$($_.Name)" | Out-Null
    Copy-Item $t "$InstallDir\themes\$($_.Name)\theme.py" -Force
  }
}
Get-ChildItem "$src\plugins\*.py" | ForEach-Object {
  $dst = Join-Path "$InstallDir\plugins" $_.Name
  if (-not (Test-Path $dst)) { Copy-Item $_.FullName $dst }
}
# scenes and signals: shipped ones are added, never overwritten (yours may be edited or added)
New-Item -ItemType Directory -Force -Path "$InstallDir\scenes", "$InstallDir\signals" | Out-Null
Get-ChildItem "$src\scenes\*.py" | ForEach-Object {
  $dst = Join-Path "$InstallDir\scenes" $_.Name
  if ($_.Name -eq "_template.py" -or -not (Test-Path $dst)) { Copy-Item $_.FullName $dst -Force }
}
Get-ChildItem "$src\signals\*.py" -ErrorAction SilentlyContinue | ForEach-Object {
  $dst = Join-Path "$InstallDir\signals" $_.Name
  if (-not (Test-Path $dst)) { Copy-Item $_.FullName $dst }
}
if (-not (Test-Path "$InstallDir\macros.yaml")) { Copy-Item "$src\macros.yaml" $InstallDir }

# 2. python venv
$venv = "$InstallDir\.venv"
if (-not (Test-Path "$venv\Scripts\python.exe")) {
  $py = (Get-Command py -ErrorAction SilentlyContinue)
  if ($py) { & py -3 -m venv $venv } else { & python -m venv $venv }
}
& "$venv\Scripts\python.exe" -m pip install -q --upgrade pip
& "$venv\Scripts\python.exe" -m pip install -q pillow pyyaml psutil nvidia-ml-py wmi pywin32

# 3. VSDinside SDK (official M18 driver + transport.dll)
$sdk = "$InstallDir\sdk"
if (Test-Path "$sdk\.git") {
  git -C $sdk pull -q
} elseif (Get-Command git -ErrorAction SilentlyContinue) {
  git clone -q --depth 1 https://github.com/VSDinside/VSDinside-Plugin-SDK $sdk
} else {
  $zip = "$env:TEMP\vsd-sdk.zip"
  Invoke-WebRequest https://github.com/VSDinside/VSDinside-Plugin-SDK/archive/refs/heads/main.zip -OutFile $zip
  Expand-Archive $zip "$env:TEMP\vsd-sdk" -Force
  Move-Item "$env:TEMP\vsd-sdk\VSDinside-Plugin-SDK-main" $sdk
}

# 4. m18ctl on PATH-friendly wrapper
"@`"$venv\Scripts\python.exe`" `"$InstallDir\m18ctl.py`" %*" | Set-Content "$InstallDir\m18ctl.cmd" -Encoding ASCII

# 5. start at login (hidden, no console window)
if (-not $NoStartup) {
  $lnk = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\M18 Macro Pad.lnk"
  $ws = New-Object -ComObject WScript.Shell
  $s = $ws.CreateShortcut($lnk)
  $s.TargetPath = "$venv\Scripts\pythonw.exe"
  $s.Arguments = "`"$InstallDir\m18d.py`""
  $s.WorkingDirectory = $InstallDir
  $s.Save()
  Write-Host "Startup shortcut: $lnk"
}

Write-Host ""
Write-Host "Done. Next:"
Write-Host "  1. Quit VSD Craft (tray icon > Exit) and stop it starting with Windows."
Write-Host "  2. $InstallDir\m18ctl.cmd doctor"
Write-Host "  3. $InstallDir\m18ctl.cmd start      (or --mock to try without the pad)"
