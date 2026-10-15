# One-step home setup for Windows. Safe to run again (it keeps your existing .env).
# Run from the Price-Sniper folder in PowerShell:
#   powershell -ExecutionPolicy Bypass -File scripts\home\setup.ps1
#   (add -NoService to install without auto-start)
param([switch]$NoService, [int]$Port = 8000)
$ErrorActionPreference = "Stop"
Set-Location (Resolve-Path "$PSScriptRoot\..\..")
$Root = (Get-Location).Path

function Say($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }

# --- 1. Python ----------------------------------------------------------------
Say "Checking Python"
$py = $null   # @(command, extra args...)
foreach ($cand in @(@("py", "-3"), @("python"))) {
    try {
        $extra = @($cand | Select-Object -Skip 1)
        $ok = & $cand[0] @extra -c "import sys; print(sys.version_info >= (3, 10))" 2>$null
        if ($ok -eq "True") { $py = $cand; break }
    } catch {}
}
if (-not $py) {
    Write-Host "Python 3.10 or newer is required. Install it from https://www.python.org/downloads/"
    Write-Host "IMPORTANT: on the first installer screen, tick 'Add python.exe to PATH'. Then run this again."
    exit 1
}
$pyExtra = @($py | Select-Object -Skip 1)
& $py[0] @pyExtra --version

# --- 2. App + dependencies in a private folder (.venv) -------------------------
Say "Installing Price Sniper and its dependencies"
if (-not (Test-Path .venv)) { & $py[0] @pyExtra -m venv .venv }
$vpy = "$Root\.venv\Scripts\python.exe"
& $vpy -m pip install -q --upgrade pip
& $vpy -m pip install -q -c constraints.txt -e ".[dev]"

# --- 3. Settings file (.env) -----------------------------------------------------
$tsUrl = ""
$ts = Get-Command tailscale -ErrorAction SilentlyContinue
if (-not $ts -and (Test-Path "C:\Program Files\Tailscale\tailscale.exe")) { $ts = "C:\Program Files\Tailscale\tailscale.exe" }
if ($ts) {
    try {
        $status = & $ts status --json | ConvertFrom-Json
        if ($status.Self.DNSName) { $tsUrl = "https://" + $status.Self.DNSName.TrimEnd(".") }
    } catch {}
}

if (-not (Test-Path .env)) {
    Say "Creating your settings file (.env)"
    Copy-Item .env.example .env
    $code = @'
import re, secrets, sys
from cryptography.fernet import Fernet
words = ["red", "fast", "gpu", "deal", "hawk", "snipe", "lucky", "pixel", "turbo", "orbit", "cobalt", "ember"]
values = {
    "SNIPER_SECRET_KEY": Fernet.generate_key().decode(),
    "SNIPER_INVITE_CODE": "-".join(secrets.choice(words) for _ in range(3)) + "-" + str(secrets.randbelow(90) + 10),
    "SNIPER_ADMIN_TOKEN": secrets.token_urlsafe(24),
}
if len(sys.argv) > 1 and sys.argv[1]:
    values["PUBLIC_URL"] = sys.argv[1]
text = open(".env", encoding="utf-8").read()
for k, v in values.items():
    text = re.sub(rf"^{k}=.*$", f"{k}={v}", text, flags=re.M)
open(".env", "w", encoding="utf-8").write(text)
'@
    if ($tsUrl) { $code | & $vpy - $tsUrl } else { $code | & $vpy - }
} else {
    Say "Keeping your existing .env"
    if ($tsUrl -and (Select-String -Path .env -Pattern '^PUBLIC_URL=http://localhost' -Quiet)) {
        (Get-Content .env) -replace '^PUBLIC_URL=.*', "PUBLIC_URL=$tsUrl" | Set-Content .env
        Write-Host "Set PUBLIC_URL=$tsUrl"
    }
}

Say "Running the self-tests"
& $vpy -m pytest -q 2>&1 | Select-Object -Last 1

# --- 4. Start automatically when you sign in to Windows ------------------------
# Listens on this computer only (127.0.0.1). Friends reach it through Tailscale, not your home network.
$pyw = "$Root\.venv\Scripts\pythonw.exe"     # no console window
$runArgs = "-m uvicorn sniper.app:app --host 127.0.0.1 --port $Port"
if ($NoService) {
    Say "Skipping auto-start (-NoService). To run it by hand: $vpy $runArgs"
} else {
    Say "Setting it to start automatically (Task Scheduler: 'Price Sniper')"
    $action = New-ScheduledTaskAction -Execute $pyw -Argument $runArgs -WorkingDirectory $Root
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
    Register-ScheduledTask -TaskName "Price Sniper" -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
    Stop-ScheduledTask -TaskName "Price Sniper" -ErrorAction SilentlyContinue
    Start-ScheduledTask -TaskName "Price Sniper"
    Write-Host "Started."
}

# --- 5. Tailscale (private access for you + friends) ------------------------------------
if ($tsUrl -and -not $NoService) {
    Say "Sharing it privately over Tailscale"
    try { & $ts serve --bg $Port } catch { Write-Host "Run this yourself if it failed: tailscale serve --bg $Port" }
}

Say "Done!"
Write-Host "On this computer:   http://localhost:$Port"
if ($tsUrl) {
    Write-Host "For you + friends:  $tsUrl   (they need Tailscale on their phone; see HOME_SETUP.md)"
} else {
    Write-Host "Tailscale isn't set up yet. Install it (https://tailscale.com/download), sign in, then run this again."
}
$invite = (Select-String -Path .env -Pattern '^SNIPER_INVITE_CODE=(.*)').Matches[0].Groups[1].Value
Write-Host "Invite code for sign-up:  $invite"
