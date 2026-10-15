#!/usr/bin/env bash
# Update a running Price Sniper with a heads-up for users.
#   scripts/update.sh [seconds] ["banner message"]
# Assumes the app runs as a systemd service named price-sniper (override with SNIPER_SERVICE).
set -euo pipefail
cd "$(dirname "$0")/.."

NOTICE_SECONDS="${1:-60}"
MESSAGE="${2:-}"
SERVICE="${SNIPER_SERVICE:-price-sniper}"

python -m sniper.maintenance announce --in "$NOTICE_SECONDS" ${MESSAGE:+--message "$MESSAGE"}
# If anything below fails, take the banner down so users aren't left waiting.
trap 'python -m sniper.maintenance cancel; echo "update failed; notice cancelled" >&2' ERR

# Fetch and install during the countdown. The running app keeps serving the old code.
git pull --ff-only
pip install -q -e .
python -m pytest -q

echo "waiting ${NOTICE_SECONDS}s for the countdown..."
sleep "$NOTICE_SECONDS"
sudo systemctl restart "$SERVICE"
trap - ERR
echo "restarted; browsers will reload themselves when the new version answers"
