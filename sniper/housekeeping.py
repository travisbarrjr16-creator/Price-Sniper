"""Daily housekeeping: trim old data so the database doesn't grow forever, and keep backups.

Runs once a day from the background loop. Also available by hand:
    python -m sniper.admin backup
"""
from __future__ import annotations

import logging
import os
import sqlite3
import tarfile
from datetime import timedelta
from pathlib import Path

from .db import DB
from .models import utcnow

log = logging.getLogger("sniper.housekeeping")

KEEP = {                       # how long to keep things
    "gone_listings_days": 60,  # listings that disappeared (sold/ended)
    "snapshots_days": 365,     # price-history points for the chart
    "alerts_days": 180,
    "usage_days": 90,
}
BACKUPS_TO_KEEP = 14


def purge(db: DB) -> dict:
    now = utcnow()
    ago = lambda days: (now - timedelta(days=days)).isoformat()  # noqa: E731
    counts = {}
    with db.lock, db.conn:
        c = db.conn
        counts["listings"] = c.execute("DELETE FROM listings WHERE active=0 AND last_seen<?",
                                       (ago(KEEP["gone_listings_days"]),)).rowcount
        counts["snapshots"] = c.execute("DELETE FROM snapshots WHERE ts<?", (ago(KEEP["snapshots_days"]),)).rowcount
        counts["alerts"] = c.execute("DELETE FROM alerts WHERE created_at<?", (ago(KEEP["alerts_days"]),)).rowcount
        counts["usage"] = c.execute("DELETE FROM api_usage WHERE day<?",
                                    ((now - timedelta(days=KEEP["usage_days"])).date().isoformat(),)).rowcount
        now_iso = now.isoformat()
        counts["sessions"] = c.execute("DELETE FROM sessions WHERE expires_at<?", (now_iso,)).rowcount
        counts["resets"] = c.execute("DELETE FROM password_resets WHERE expires_at<?", (now_iso,)).rowcount
    return counts


def backup(db: DB, dest: Path | None = None, keep: int = BACKUPS_TO_KEEP) -> Path:
    """One .tar.gz with a consistent copy of the database, .env, and keys/ (safe while the app runs).

    These stay on the same computer: copy the backups/ folder somewhere else now and then.
    """
    dest = Path(dest or os.getenv("SNIPER_BACKUP_DIR", "backups"))
    dest.mkdir(parents=True, exist_ok=True)
    stamp = utcnow().strftime("%Y%m%d-%H%M%S-%f")     # unique even for two backups in one second
    snap = dest / f".sniper-{stamp}.db"
    with db.lock:
        out = sqlite3.connect(snap)
        db.conn.backup(out)          # SQLite's online backup: consistent even mid-write
        out.close()
    archive = dest / f"price-sniper-backup-{stamp}.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(snap, arcname="sniper.db")
        for extra in (".env", ".sniper_secret", "keys"):
            if Path(extra).exists():
                tar.add(extra, arcname=extra)
    snap.unlink()
    os.chmod(archive, 0o600)         # it contains your keys
    old = sorted(dest.glob("price-sniper-backup-*.tar.gz"))[:-keep]
    for f in old:
        f.unlink()
    return archive


def daily(db: DB) -> None:
    try:
        log.info("purged old data: %s", purge(db))
        log.info("backup written: %s", backup(db))
    except Exception:
        log.exception("housekeeping failed")
