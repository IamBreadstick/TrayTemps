$ErrorActionPreference = "Stop"

$helperExe = Join-Path $PSScriptRoot "sensor_helper\publish\TempTray.SensorHelper.exe"
if (!(Test-Path $helperExe)) {
    Write-Host "Bundled sensor helper is missing. Building it first..."
    & (Join-Path $PSScriptRoot "build_sensor_helper.ps1")
}

$pythonCmd = $null
foreach ($candidate in @("py", "python", "python3")) {
    $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
    if ($cmd) {
        $pythonCmd = $candidate
        break
    }
}

if (!$pythonCmd) {
    throw "Python was not found. Install Python 3.11+ from python.org, check 'Add python.exe to PATH', reopen PowerShell, then run .\build.ps1 again."
}

if (!(Test-Path ".venv")) {
    & $pythonCmd -m venv .venv
}

.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m PyInstaller TrayTemps.spec

Write-Host ""
Write-Host "Build complete. Executable: dist\TrayTemps\TrayTemps.exe"
Write-Host "For GitHub releases, zip the entire dist\TrayTemps folder. End users should run TrayTemps.exe."
