"""Heads-up notices before an update takes the app down.

From the server (e.g. in a deploy script):
    python -m sniper.maintenance announce --in 60
    python -m sniper.maintenance cancel
    python -m sniper.maintenance status

Every open browser shows a countdown banner, then reloads itself once the
new version is up.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta
from typing import Optional

from .db import DB
from .models import utcnow

KEY = "maintenance"
STALE_AFTER = 300   # still running 5 min after the deadline = the update didn't happen; drop the banner
DEFAULT_MESSAGE = "Price Sniper is updating. It'll be back in a minute or two, and your watches are saved."


def announce(db: DB, seconds: int = 60, message: Optional[str] = None) -> dict:
    notice = {"at": (utcnow() + timedelta(seconds=seconds)).isoformat(), "message": message or DEFAULT_MESSAGE}
    db.set_setting(KEY, json.dumps(notice))
    return notice


def cancel(db: DB) -> None:
    db.set_setting(KEY, None)


def current(db: DB) -> Optional[dict]:
    raw = db.get_setting(KEY)
    if not raw:
        return None
    notice = json.loads(raw)
    left = (datetime.fromisoformat(notice["at"]) - utcnow()).total_seconds()
    if left < -STALE_AFTER:
        cancel(db)          # otherwise everyone sees "Updating now…" forever
        return None
    return {**notice, "seconds_left": max(0, int(left))}


def clear_on_startup(db: DB) -> None:
    """Called when the server starts: a restart means the update has happened."""
    cancel(db)


def main(argv: Optional[list[str]] = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m sniper.maintenance")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("announce", help="warn users that the app goes down soon")
    a.add_argument("--in", dest="seconds", type=int, default=60, help="seconds until downtime (default 60)")
    a.add_argument("--message", help="text shown in the banner")
    sub.add_parser("cancel", help="remove the notice")
    sub.add_parser("status", help="show the current notice")
    args = ap.parse_args(argv)
    db = DB()
    if args.cmd == "announce":
        print("announced:", announce(db, args.seconds, args.message))
    elif args.cmd == "cancel":
        cancel(db)
        print("cancelled")
    else:
        print(current(db) or "no notice")


if __name__ == "__main__":
    main()
