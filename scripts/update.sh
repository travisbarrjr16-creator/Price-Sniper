#!/usr/bin/env bash
# Update a running Price Sniper with a heads-up for users (Mac / Linux).
#   scripts/update.sh [seconds] ["banner message"]
# Restarts whichever way it was installed: launchd (Mac), systemd user service (home Linux),
# or a system service named $SNIPER_SERVICE (rented server, default price-sniper).
set -Eeuo pipefail   # -E: failures inside restart_app also cancel the banner
cd "$(dirname "$0")/.."

NOTICE_SECONDS="${1:-60}"
MESSAGE="${2:-}"
PY=python3
[[ -x .venv/bin/python ]] && PY=.venv/bin/python

restart_app() {
  if [[ "$(uname)" == "Darwin" ]] && [[ -f "$HOME/Library/LaunchAgents/com.pricesniper.app.plist" ]]; then
    launchctl kickstart -k "gui/$(id -u)/com.pricesniper.app"
  elif systemctl --user cat price-sniper.service >/dev/null 2>&1; then
    systemctl --user restart price-sniper.service
  else
    sudo systemctl restart "${SNIPER_SERVICE:-price-sniper}"
  fi
}

"$PY" -m sniper.maintenance announce --in "$NOTICE_SECONDS" ${MESSAGE:+--message "$MESSAGE"}
# If anything below fails, take the banner down so users aren't left waiting.
trap '"$PY" -m sniper.maintenance cancel; echo "update failed; notice cancelled, old version still running" >&2' ERR

# Fetch and install during the countdown. The running app keeps serving the old code.
git pull --ff-only
"$PY" -m pip install -q -e ".[dev]"
"$PY" -m pytest -q

echo "waiting ${NOTICE_SECONDS}s for the countdown..."
sleep "$NOTICE_SECONDS"
restart_app
trap - ERR
echo "restarted; browsers will reload themselves when the new version answers"
