<#
Installs Lumina Lite to %USERPROFILE%\LuminaLite and creates shortcuts.

  .\install.ps1            install or update, Start Menu and Desktop shortcuts
  .\install.ps1 -Startup   also start minimised at login (replaces the original
                           Lumina.lnk in the Startup folder, keeping a backup)

Needs uv (https://docs.astral.sh/uv/).

Why not %LOCALAPPDATA%: when this script runs from a packaged (MSIX) app, such
as the Claude desktop app, Windows silently redirects writes under AppData into
that app's private storage. Explorer then can't see the install and every
shortcut breaks. A folder in the user profile is never redirected.
The path is also kept short because MediaPipe fails to load its DLL from paths
longer than 260 characters.
#>
param([switch]$Startup, [string]$Destination = (Join-Path $env:USERPROFILE "LuminaLite"))
$ErrorActionPreference = "Stop"

$src = $PSScriptRoot
$dst = $Destination
$py  = Join-Path $dst "venv\Scripts\python.exe"
$exe = Join-Path $dst "Lumina Lite.exe"

# Stop a running copy so its files can be replaced.
Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" |
    Where-Object CommandLine -match 'LuminaLite' |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force }

New-Item -ItemType Directory -Force $dst | Out-Null
Copy-Item (Join-Path $src "*.py"), (Join-Path $src "README.md") $dst -Force

# Carry over settings and the Lumina.lnk backup from the old AppData locations.
$oldDirs = @(
    (Join-Path $env:APPDATA "LuminaLite"),
    (Join-Path $env:LOCALAPPDATA "LuminaLite")
)
foreach ($old in $oldDirs) {
    foreach ($name in "settings.json", "Lumina.lnk.original-startup") {
        $f = Join-Path $old $name
        if ((Test-Path $f) -and -not (Test-Path (Join-Path $dst $name))) { Copy-Item $f $dst }
    }
}

if (-not (Test-Path $py)) { uv venv (Join-Path $dst "venv") --python 3.12 -q }
uv pip install --python $py -q -r (Join-Path $src "requirements.txt")

# Build "Lumina Lite.exe" with the C# compiler that ships with Windows.
$csc = Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319\csc.exe"
$lumina = "C:\Program Files\Lumina\LuminaStudioApp.exe"
$iconArg = @()
if (Test-Path $lumina) {
    Add-Type -AssemblyName System.Drawing
    $ico = Join-Path $dst "lumina.ico"
    $fs = [IO.File]::Create($ico)
    [Drawing.Icon]::ExtractAssociatedIcon($lumina).Save($fs)
    $fs.Close()
    $iconArg = @("/win32icon:$ico")
}
& $csc /nologo /target:winexe /optimize "/out:$exe" @iconArg /reference:System.Windows.Forms.dll (Join-Path $src "launcher.cs")
if ($LASTEXITCODE -ne 0) { throw "Building Lumina Lite.exe failed" }

$ws = New-Object -ComObject WScript.Shell
function New-Shortcut($path, $extraArgs) {
    $l = $ws.CreateShortcut($path)
    $l.TargetPath = $exe
    $l.Arguments = $extraArgs
    $l.WorkingDirectory = $dst
    $l.IconLocation = "$exe,0"
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
