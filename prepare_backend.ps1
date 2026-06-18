$ErrorActionPreference = "Stop"

$repo = "LibreHardwareMonitor/LibreHardwareMonitor"
$backendRoot = Join-Path $PSScriptRoot "backend\LibreHardwareMonitor"
$tmp = Join-Path $env:TEMP "TrayTemps-LibreHardwareMonitor"
$zipPath = Join-Path $tmp "LibreHardwareMonitor.zip"

New-Item -ItemType Directory -Force -Path $backendRoot | Out-Null
New-Item -ItemType Directory -Force -Path $tmp | Out-Null

Write-Host "Fetching latest LibreHardwareMonitor release metadata..."
$release = Invoke-RestMethod -Uri "https://api.github.com/repos/$repo/releases/latest" -Headers @{ "User-Agent" = "TrayTemps-build-script" }
$asset = $release.assets | Where-Object { $_.name -match "(?i)LibreHardwareMonitor.*\.zip$" } | Select-Object -First 1

if ($null -eq $asset) {
    throw "Could not find a LibreHardwareMonitor ZIP asset in the latest GitHub release. Download it manually and extract it into backend\LibreHardwareMonitor."
}

Write-Host "Downloading $($asset.name)..."
Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $zipPath -Headers @{ "User-Agent" = "TrayTemps-build-script" }

$extract = Join-Path $tmp "extract"
if (Test-Path $extract) { Remove-Item -Recurse -Force $extract }
New-Item -ItemType Directory -Force -Path $extract | Out-Null
Expand-Archive -Path $zipPath -DestinationPath $extract -Force

# The release ZIP may contain files directly or inside a top-level folder.
$exe = Get-ChildItem -Path $extract -Recurse -Filter "LibreHardwareMonitor.exe" | Select-Object -First 1
if ($null -eq $exe) {
    throw "Downloaded release did not contain LibreHardwareMonitor.exe."
}

Write-Host "Copying backend files..."
Get-ChildItem -Path $backendRoot -Force | Remove-Item -Recurse -Force
Copy-Item -Path (Join-Path $exe.Directory.FullName "*") -Destination $backendRoot -Recurse -Force

if (!(Test-Path (Join-Path $backendRoot "LibreHardwareMonitor.exe"))) {
    throw "Backend preparation failed: LibreHardwareMonitor.exe is missing."
}


$inspection = Join-Path $backendRoot "TRAYTEMPS_LOW_LEVEL_INSPECTION.txt"
"TrayTemps LibreHardwareMonitor backend inspection" | Set-Content -Path $inspection -Encoding UTF8
Add-Content -Path $inspection -Value ("Created: {0}" -f (Get-Date).ToString("s"))
Add-Content -Path $inspection -Value ("BackendRoot: {0}" -f $backendRoot)
Add-Content -Path $inspection -Value ""
Add-Content -Path $inspection -Value "Low-level candidate files copied from the official LibreHardwareMonitor release:"
$lowLevel = Get-ChildItem -Path $backendRoot -Recurse -File -ErrorAction SilentlyContinue | Where-Object { $_.Name -match "(?i)(pawn|winring|openlibsys|\\.sys$|\\.inf$|\\.cat$)" }
if ($lowLevel.Count -eq 0) {
    Add-Content -Path $inspection -Value "LOW_LEVEL_ASSETS|none"
} else {
    foreach ($item in $lowLevel) {
        Add-Content -Path $inspection -Value ("LOW_LEVEL_ASSET|{0}|bytes={1}" -f $item.FullName, $item.Length)
    }
}

Write-Host "Backend ready: $backendRoot"
Write-Host "Low-level inspection: $inspection"
