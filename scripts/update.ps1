# Update a running Price Sniper with a heads-up for users (Windows).
#   powershell -ExecutionPolicy Bypass -File scripts\update.ps1 [-Seconds 60] [-Message "..."]
param([int]$Seconds = 60, [string]$Message = "")
$ErrorActionPreference = "Stop"
Set-Location (Resolve-Path "$PSScriptRoot\..")
$vpy = ".\.venv\Scripts\python.exe"

$oldCommit = (git rev-parse HEAD).Trim()
$start = Get-Date
if ($Message) { & $vpy -m sniper.maintenance announce --in $Seconds --message $Message }
else { & $vpy -m sniper.maintenance announce --in $Seconds }

try {
    # Fetch and install during the countdown. The running app keeps serving the old code.
    git pull --ff-only;               if ($LASTEXITCODE) { throw "git pull failed" }
    & $vpy -m pip install -q -c constraints.txt -e ".[dev]"; if ($LASTEXITCODE) { throw "install failed" }
    & $vpy -m pytest -q;              if ($LASTEXITCODE) { throw "tests failed" }
} catch {
    & $vpy -m sniper.maintenance cancel
    # Put the code back, so a later restart never runs a version that failed its tests
    if ((git rev-parse HEAD).Trim() -ne $oldCommit) {
        git reset -q --hard $oldCommit
        & $vpy -m pip install -q -c constraints.txt -e ".[dev]"
    }
    Write-Host "Update failed ($_). Notice cancelled; the old version is still running." -ForegroundColor Red
    exit 1
}

$left = [int]($Seconds - ((Get-Date) - $start).TotalSeconds)   # the countdown kept running during the install
if ($left -gt 0) {
    Write-Host "Waiting $left seconds for the countdown..."
    Start-Sleep -Seconds $left
}
Stop-ScheduledTask -TaskName "Price Sniper"
Start-ScheduledTask -TaskName "Price Sniper"
Write-Host "Restarted; browsers will reload themselves when the new version answers."
