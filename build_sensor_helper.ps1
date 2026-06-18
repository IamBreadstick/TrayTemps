$ErrorActionPreference = "Stop"

$backendRoot = Join-Path $PSScriptRoot "backend\LibreHardwareMonitor"
$backendDll = Join-Path $backendRoot "LibreHardwareMonitorLib.dll"
if (!(Test-Path $backendDll)) {
    Write-Host "LibreHardwareMonitorLib.dll is missing. Preparing backend files first..."
    & (Join-Path $PSScriptRoot "prepare_backend.ps1")
}

# Minimal files required by the TrayTemps helper. Do not ship the full LHM GUI
# release folder in the app; keep source/build diagnostics separate from release output.
$requiredRuntimeFiles = @(
    "LibreHardwareMonitorLib.dll",
    "System.Management.dll"
)
$optionalRuntimeFiles = @(
    "HidSharp.dll",
    "RAMSPDToolkit-NDD.dll",
    "Iot.Device.Bindings.dll",
    "System.Device.Gpio.dll"
)
foreach ($file in $requiredRuntimeFiles) {
    if (!(Test-Path (Join-Path $backendRoot $file))) {
        throw "Required LibreHardwareMonitor runtime file is missing: $file. Run .\prepare_backend.ps1 again."
    }
}

if ($null -eq (Get-Command dotnet -ErrorAction SilentlyContinue)) {
    throw "The .NET SDK is required to build the sensor helper. Install the .NET SDK, not just the runtime, then run this again. End users do not need the SDK once you publish a release ZIP."
}

$project = Join-Path $PSScriptRoot "sensor_helper\TempTray.SensorHelper\TempTray.SensorHelper.csproj"
$publish = Join-Path $PSScriptRoot "sensor_helper\publish"
if (Test-Path $publish) { Remove-Item -Recurse -Force $publish }

Write-Host "Publishing self-contained sensor helper..."
dotnet publish $project -c Release -r win-x64 --self-contained true -o $publish /p:PublishSingleFile=false /p:PublishTrimmed=false

Write-Host "Copying minimal LibreHardwareMonitor runtime files beside helper..."
foreach ($file in ($requiredRuntimeFiles + $optionalRuntimeFiles)) {
    $src = Join-Path $backendRoot $file
    if (Test-Path $src) {
        Copy-Item -Path $src -Destination (Join-Path $publish $file) -Force
    }
}

foreach ($file in $requiredRuntimeFiles) {
    if (!(Test-Path (Join-Path $publish $file))) {
        throw "Publish dependency check failed: $file was not copied to sensor_helper\publish."
    }
}

if (!(Test-Path (Join-Path $publish "TempTray.SensorHelper.exe"))) {
    throw "Sensor helper publish failed."
}

$manifest = Join-Path $publish "TRAYTEMPS_RUNTIME_MANIFEST.txt"
"TrayTemps minimal runtime manifest" | Set-Content -Path $manifest -Encoding UTF8
Add-Content -Path $manifest -Value ("Created: {0}" -f (Get-Date).ToString("s"))
Add-Content -Path $manifest -Value ("BackendRoot: {0}" -f $backendRoot)
Add-Content -Path $manifest -Value ("PublishRoot: {0}" -f $publish)
Add-Content -Path $manifest -Value ""
foreach ($file in ($requiredRuntimeFiles + $optionalRuntimeFiles)) {
    $dst = Join-Path $publish $file
    if (Test-Path $dst) {
        Add-Content -Path $manifest -Value ("RUNTIME_FILE|{0}|bytes={1}|sha256={2}" -f $file, (Get-Item $dst).Length, (Get-FileHash -Algorithm SHA256 -Path $dst).Hash)
    } else {
        Add-Content -Path $manifest -Value ("RUNTIME_FILE|{0}|missing" -f $file)
    }
}

Write-Host "Sensor helper ready: $publish"
Write-Host "Runtime manifest: $manifest"
