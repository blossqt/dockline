# Builds dist\Dockline\Dockline.exe (build.py does the work: an import check, the icon, PyInstaller,
# then the exe's --self-test). Run by release.py; needs bootstrap.ps1.
#
#   powershell -ExecutionPolicy Bypass -File build\build_app.ps1

$Py = Join-Path $env:LOCALAPPDATA 'DocklineBuild\venv\Scripts\python.exe'
if (-not (Test-Path $Py)) { Write-Host 'ERROR: venv missing - run build\bootstrap.ps1 first' -ForegroundColor Red; exit 1 }
$env:DOCKLINE_PYTHON = $Py
& $Py (Join-Path $PSScriptRoot 'build.py')
exit $LASTEXITCODE
