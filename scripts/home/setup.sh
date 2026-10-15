#!/usr/bin/env bash
# One-step home setup for Mac or Linux. Safe to run again (it keeps your existing .env).
#   scripts/home/setup.sh               install + start automatically at login/boot
#   scripts/home/setup.sh --no-service  install only (for testing)
set -euo pipefail
cd "$(dirname "$0")/../.."
ROOT="$(pwd)"
PORT="${SNIPER_PORT:-8000}"
NO_SERVICE=0
[[ "${1:-}" == "--no-service" ]] && NO_SERVICE=1

say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

# --- 1. Python ----------------------------------------------------------------
say "Checking Python"
PY="$(command -v python3 || true)"
if [[ -z "$PY" ]] || ! "$PY" -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then
  echo "Python 3.10 or newer is required."
  [[ "$(uname)" == "Darwin" ]] && echo "Install it from https://www.python.org/downloads/ (or: brew install python), then run this again."
  [[ "$(uname)" == "Linux" ]] && echo "Install it with your package manager, e.g.: sudo apt install python3 python3-venv, then run this again."
  exit 1
fi
"$PY" --version

# Small servers (like Google Cloud's free e2-micro, 1 GB RAM) can run out of memory installing packages.
if [[ "$(uname)" == "Linux" ]] && [[ -r /proc/meminfo ]]; then
  MEM_MB=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)
  SWAP_MB=$(awk '/SwapTotal/ {print int($2/1024)}' /proc/meminfo)
  if (( MEM_MB < 1500 && SWAP_MB < 512 )); then
    say "Low memory (${MEM_MB} MB, no swap). If the install gets killed, add a 2 GB swap file first:"
    echo "  sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile"
    echo "  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab"
  fi
fi

# --- 2. App + dependencies in a private folder (.venv) -------------------------
say "Installing Price Sniper and its dependencies"
[[ -d .venv ]] || "$PY" -m venv .venv
.venv/bin/python -m pip install -q --upgrade pip
.venv/bin/python -m pip install -q --no-cache-dir -c constraints.txt -e ".[dev]"

# --- 3. Settings file (.env) -----------------------------------------------------
TS_URL=""
if command -v tailscale >/dev/null 2>&1; then
  TS_NAME="$(tailscale status --json 2>/dev/null | .venv/bin/python -c 'import sys,json; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))' 2>/dev/null || true)"
  [[ -n "$TS_NAME" ]] && TS_URL="https://$TS_NAME"
fi

if [[ ! -f .env ]]; then
  say "Creating your settings file (.env)"
  cp .env.example .env
  .venv/bin/python - "$TS_URL" <<'PY'
import re, secrets, sys
from cryptography.fernet import Fernet
words = ["red", "fast", "gpu", "deal", "hawk", "snipe", "lucky", "pixel", "turbo", "orbit", "cobalt", "ember"]
values = {
    "SNIPER_SECRET_KEY": Fernet.generate_key().decode(),
    "SNIPER_INVITE_CODE": "-".join(secrets.choice(words) for _ in range(3)) + "-" + str(secrets.randbelow(90) + 10),
    "SNIPER_ADMIN_TOKEN": secrets.token_urlsafe(24),
}
if sys.argv[1]:
    values["PUBLIC_URL"] = sys.argv[1]
text = open(".env", encoding="utf-8").read()
for k, v in values.items():
    text = re.sub(rf"^{k}=.*$", f"{k}={v}", text, flags=re.M)
open(".env", "w", encoding="utf-8").write(text)
PY
  chmod 600 .env
else
  say "Keeping your existing .env"
  if [[ -n "$TS_URL" ]] && grep -q '^PUBLIC_URL=http://localhost' .env; then
    sed -i.bak "s#^PUBLIC_URL=.*#PUBLIC_URL=$TS_URL#" .env && rm -f .env.bak
    echo "Set PUBLIC_URL=$TS_URL"
  fi
fi

say "Running the self-tests"
.venv/bin/python -m pytest -q 2>&1 | tail -1

# --- 4. Start automatically ----------------------------------------------------------
# Listens on this computer only (127.0.0.1). Friends reach it through Tailscale, not your home network.
RUN=("$ROOT/.venv/bin/python" -m uvicorn sniper.app:app --host 127.0.0.1 --port "$PORT")

if [[ $NO_SERVICE == 1 ]]; then
  say "Skipping auto-start (--no-service). To run it by hand: ${RUN[*]}"
elif [[ "$(uname)" == "Darwin" ]]; then
  say "Setting it to start automatically (macOS launchd)"
  PLIST="$HOME/Library/LaunchAgents/com.pricesniper.app.plist"
  mkdir -p "$HOME/Library/LaunchAgents" "$ROOT/logs"
  cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.pricesniper.app</string>
  <key>ProgramArguments</key><array>
$(for a in "${RUN[@]}"; do echo "    <string>$a</string>"; done)
  </array>
  <key>WorkingDirectory</key><string>$ROOT</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$ROOT/logs/sniper.log</string>
  <key>StandardErrorPath</key><string>$ROOT/logs/sniper.log</string>
</dict></plist>
EOF
  launchctl bootout "gui/$(id -u)/com.pricesniper.app" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$PLIST"
  echo "Started. Logs: $ROOT/logs/sniper.log"
else
  say "Setting it to start automatically (systemd user service)"
  UNIT_DIR="$HOME/.config/systemd/user"
  mkdir -p "$UNIT_DIR"
  cat > "$UNIT_DIR/price-sniper.service" <<EOF
[Unit]
Description=Price Sniper deal watcher
After=network-online.target

[Service]
WorkingDirectory=$ROOT
ExecStart=${RUN[*]}
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
EOF
  systemctl --user daemon-reload
  systemctl --user enable --now price-sniper.service
  # Keep running after you log out / after a reboot without logging in.
  loginctl enable-linger "$USER" 2>/dev/null || echo "(Optional) run: sudo loginctl enable-linger $USER  so it starts at boot without logging in"
  echo "Started. Logs: journalctl --user -u price-sniper -f"
fi

# --- 5. Tailscale (private access for you + friends) ------------------------------------
if [[ -n "$TS_URL" && $NO_SERVICE == 0 ]]; then
  say "Sharing it privately over Tailscale"
  tailscale serve --bg "$PORT" || echo "Run this yourself if it failed: tailscale serve --bg $PORT"
fi

say "Done!"
echo "On this computer:   http://localhost:$PORT"
if [[ -n "$TS_URL" ]]; then
  echo "For you + friends:  $TS_URL   (they need Tailscale on their phone; see HOME_SETUP.md)"
else
  echo "Tailscale isn't set up yet. Install it (https://tailscale.com/download), sign in, then run this script again."
fi
echo "Invite code for sign-up:  $(grep '^SNIPER_INVITE_CODE=' .env | cut -d= -f2)"
