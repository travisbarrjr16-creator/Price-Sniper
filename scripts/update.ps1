# Update a running Price Sniper with a heads-up for users (Windows).
#   powershell -ExecutionPolicy Bypass -File scripts\update.ps1 [-Seconds 60] [-Message "..."]
param([int]$Seconds = 60, [string]$Message = "")
$ErrorActionPreference = "Stop"
Set-Location (Resolve-Path "$PSScriptRoot\..")
$vpy = ".\.venv\Scripts\python.exe"

if ($Message) { & $vpy -m sniper.maintenance announce --in $Seconds --message $Message }
else { & $vpy -m sniper.maintenance announce --in $Seconds }

try {
    # Fetch and install during the countdown. The running app keeps serving the old code.
    git pull --ff-only;               if ($LASTEXITCODE) { throw "git pull failed" }
    & $vpy -m pip install -q -e ".[dev]"; if ($LASTEXITCODE) { throw "install failed" }
    & $vpy -m pytest -q;              if ($LASTEXITCODE) { throw "tests failed" }
} catch {
    & $vpy -m sniper.maintenance cancel
    Write-Host "Update failed ($_). Notice cancelled; the old version is still running." -ForegroundColor Red
    exit 1
}

Write-Host "Waiting $Seconds seconds for the countdown..."
Start-Sleep -Seconds $Seconds
Stop-ScheduledTask -TaskName "Price Sniper"
Start-ScheduledTask -TaskName "Price Sniper"
Write-Host "Restarted; browsers will reload themselves when the new version answers."
