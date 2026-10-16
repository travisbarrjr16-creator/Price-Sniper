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

OLD_COMMIT="$(git rev-parse HEAD)"
START=$(date +%s)
"$PY" -m sniper.maintenance announce --in "$NOTICE_SECONDS" ${MESSAGE:+--message "$MESSAGE"}

rollback() {
  # Take the banner down, and put the code back the way it was, so a later restart or reboot
  # never runs a version that failed its tests.
  "$PY" -m sniper.maintenance cancel || true
  if [[ "$(git rev-parse HEAD)" != "$OLD_COMMIT" ]]; then
    git reset -q --hard "$OLD_COMMIT" && "$PY" -m pip install -q -c constraints.txt -e ".[dev]" || true
  fi
  echo "update failed; notice cancelled, the old version is still running (code put back to ${OLD_COMMIT:0:7})" >&2
}
trap rollback ERR

# Fetch and install during the countdown. The running app keeps serving the old code.
git pull --ff-only
"$PY" -m pip install -q -c constraints.txt -e ".[dev]"
"$PY" -m pytest -q

LEFT=$(( NOTICE_SECONDS - ($(date +%s) - START) ))     # the countdown has been running meanwhile
if (( LEFT > 0 )); then
  echo "waiting ${LEFT}s for the countdown..."
  sleep "$LEFT"
fi
restart_app
trap - ERR
echo "restarted; browsers will reload themselves when the new version answers"
