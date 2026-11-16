# Sets up Dockline's build in %LOCALAPPDATA%\DocklineBuild, so the project folder stays small.
# Safe to run again.
#
#   powershell -ExecutionPolicy Bypass -File build\bootstrap.ps1
#
#   venv\   Python 3.12 with PySide6-Essentials and PyInstaller (requirements.txt)

$ErrorActionPreference = 'Stop'
$Cache = Join-Path $env:LOCALAPPDATA 'DocklineBuild'
New-Item -ItemType Directory -Force $Cache | Out-Null
$Py = Join-Path $Cache 'venv\Scripts\python.exe'
if (-not (Test-Path $Py)) {
    Write-Host '>> venv'
    & py -3.12 -m venv (Join-Path $Cache 'venv')
}
& $Py -m pip install -q -r (Join-Path $PSScriptRoot 'requirements.txt')
Write-Host ">> ready: $Py"
