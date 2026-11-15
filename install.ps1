<#
Installs Lumina Lite to %LOCALAPPDATA%\LuminaLite and creates shortcuts.

  .\install.ps1            install or update, Start Menu and Desktop shortcuts
  .\install.ps1 -Startup   also start minimised at login (replaces the original
                           Lumina.lnk in the Startup folder, keeping a backup)

Needs uv (https://docs.astral.sh/uv/). The venv lives on a short path because
MediaPipe fails to load its DLL from paths longer than 260 characters.
#>
param([switch]$Startup)
$ErrorActionPreference = "Stop"

$src = $PSScriptRoot
$dst = Join-Path $env:LOCALAPPDATA "LuminaLite"
$py  = Join-Path $dst "venv\Scripts\python.exe"
$pyw = Join-Path $dst "venv\Scripts\pythonw.exe"

# Stop a running copy so its files can be replaced.
Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" |
    Where-Object CommandLine -match 'LuminaLite' |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force }

New-Item -ItemType Directory -Force $dst | Out-Null
Copy-Item (Join-Path $src "*.py"), (Join-Path $src "README.md") $dst -Force

if (-not (Test-Path $py)) { uv venv (Join-Path $dst "venv") --python 3.12 -q }
uv pip install --python $py -q -r (Join-Path $src "requirements.txt")

$icon = "C:\Program Files\Lumina\LuminaStudioApp.exe,0"
$ws = New-Object -ComObject WScript.Shell
function New-Shortcut($path, $extraArgs) {
    $l = $ws.CreateShortcut($path)
    $l.TargetPath = $pyw
    $l.Arguments = "`"$dst\app.py`" $extraArgs".Trim()
    $l.WorkingDirectory = $dst
    if (Test-Path "C:\Program Files\Lumina\LuminaStudioApp.exe") { $l.IconLocation = $icon }
    $l.Description = "Lumina webcam effects"
    $l.Save()
}
New-Shortcut (Join-Path ([Environment]::GetFolderPath('Programs')) "Lumina Lite.lnk") ""
New-Shortcut (Join-Path ([Environment]::GetFolderPath('Desktop')) "Lumina Lite.lnk") ""

if ($Startup) {
    $su = [Environment]::GetFolderPath('Startup')
    $old = Join-Path $su "Lumina.lnk"
    if (Test-Path $old) { Move-Item $old (Join-Path $dst "Lumina.lnk.original-startup") -Force }
    New-Shortcut (Join-Path $su "Lumina Lite.lnk") "--minimized"
}

Write-Host "Installed to $dst"
