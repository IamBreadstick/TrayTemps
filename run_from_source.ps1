$ErrorActionPreference = "Stop"

$helperExe = Join-Path $PSScriptRoot "sensor_helper\publish\TempTray.SensorHelper.exe"
if (!(Test-Path $helperExe)) {
    Write-Host "Bundled sensor helper is missing. Building it first..."
    & (Join-Path $PSScriptRoot "build_sensor_helper.ps1")
}

if (!(Test-Path ".venv")) {
    py -m venv .venv
}

.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
python -m temp_tray.main
