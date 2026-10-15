"""SQLite persistence: watches, every listing seen, alerts, price snapshots."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from typing import Optional

from .models import Watch, utcnow

SCHEMA = """
CREATE TABLE IF NOT EXISTS watches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    data TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    last_checked_at TEXT,
    last_error TEXT
);
CREATE TABLE IF NOT EXISTS listings (
    watch_id INTEGER NOT NULL,
    key TEXT NOT NULL,
    data TEXT NOT NULL,           -- the full scored listing as JSON
    total REAL NOT NULL,
    effective REAL NOT NULL,
    bucket TEXT NOT NULL,         -- 'new' or 'used' (for price history)
    risk INTEGER NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    first_price REAL NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    alerted_total REAL,           -- total at which we last alerted, if ever
    PRIMARY KEY (watch_id, key)
);
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    watch_id INTEGER NOT NULL,
    key TEXT NOT NULL,
    reason TEXT NOT NULL,
    title TEXT NOT NULL,
    total REAL NOT NULL,
    url TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS snapshots (
    watch_id INTEGER NOT NULL,
    ts TEXT NOT NULL,
    bucket TEXT NOT NULL,
    min_total REAL NOT NULL,
    median_total REAL NOT NULL,
    count INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_snapshots ON snapshots(watch_id, ts);
"""


class DB:
    def __init__(self, path: Optional[str] = None) -> None:
        self.path = path or os.getenv("SNIPER_DB", "sniper.db")
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock:
            self.conn.executescript(SCHEMA)

    # --- watches -----------------------------------------------------------
    _WATCH_META = ("id", "active", "created_at", "last_checked_at", "last_error")

    def _row_to_watch(self, r: sqlite3.Row) -> Watch:
        d = json.loads(r["data"])
        d.update({k: r[k] for k in self._WATCH_META})
        d["active"] = bool(d["active"])
        return Watch(**d)

    def create_watch(self, w: Watch) -> Watch:
        data = {k: v for k, v in w.to_dict().items() if k not in self._WATCH_META}
        with self.lock, self.conn:
            cur = self.conn.execute(
                "INSERT INTO watches (data, active, created_at) VALUES (?, ?, ?)",
                (json.dumps(data), int(w.active), utcnow().isoformat()),
            )
        return self.get_watch(cur.lastrowid)

    def update_watch(self, w: Watch) -> Watch:
        data = {k: v for k, v in w.to_dict().items() if k not in self._WATCH_META}
        with self.lock, self.conn:
            self.conn.execute(
                "UPDATE watches SET data=?, active=?, last_checked_at=?, last_error=? WHERE id=?",
                (json.dumps(data), int(w.active), w.last_checked_at, w.last_error, w.id),
            )
        return self.get_watch(w.id)

    def get_watch(self, wid: int) -> Optional[Watch]:
        r = self.conn.execute("SELECT * FROM watches WHERE id=?", (wid,)).fetchone()
        return self._row_to_watch(r) if r else None

    def list_watches(self) -> list[Watch]:
        return [self._row_to_watch(r) for r in self.conn.execute("SELECT * FROM watches ORDER BY id")]

    def delete_watch(self, wid: int) -> None:
        with self.lock, self.conn:
            for t in ("watches",):
                self.conn.execute(f"DELETE FROM {t} WHERE id=?", (wid,))
            for t in ("listings", "alerts", "snapshots"):
                self.conn.execute(f"DELETE FROM {t} WHERE watch_id=?", (wid,))

    # --- listings ----------------------------------------------------------
    def get_listing_rows(self, wid: int, active_only: bool = True) -> list[sqlite3.Row]:
        q = "SELECT * FROM listings WHERE watch_id=?" + (" AND active=1" if active_only else "")
        return self.conn.execute(q, (wid,)).fetchall()

    def existing_listings(self, wid: int) -> dict[str, sqlite3.Row]:
        return {r["key"]: r for r in self.get_listing_rows(wid, active_only=False)}

    def upsert_listing(self, wid: int, key: str, data: dict, total: float, effective: float,
                       bucket: str, risk: int, now: str) -> None:
        with self.lock, self.conn:
            self.conn.execute(
                """INSERT INTO listings (watch_id, key, data, total, effective, bucket, risk, first_seen, last_seen, first_price, active)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                   ON CONFLICT(watch_id, key) DO UPDATE SET
                     data=excluded.data, total=excluded.total, effective=excluded.effective,
                     bucket=excluded.bucket, risk=excluded.risk, last_seen=excluded.last_seen, active=1""",
                (wid, key, json.dumps(data), total, effective, bucket, risk, now, now, total),
            )

    def mark_inactive_except(self, wid: int, keep: set[str]) -> None:
        with self.lock, self.conn:
            rows = self.conn.execute("SELECT key FROM listings WHERE watch_id=? AND active=1", (wid,)).fetchall()
            gone = [r["key"] for r in rows if r["key"] not in keep]
            self.conn.executemany("UPDATE listings SET active=0 WHERE watch_id=? AND key=?", [(wid, k) for k in gone])

    def set_alerted(self, wid: int, key: str, total: float) -> None:
        with self.lock, self.conn:
            self.conn.execute("UPDATE listings SET alerted_total=? WHERE watch_id=? AND key=?", (total, wid, key))

    def reference_totals(self, wid: int, bucket: str, since: str, max_risk: int) -> list[float]:
        """Totals of every distinct, non-risky listing seen recently (active or gone)."""
        rows = self.conn.execute(
            "SELECT total FROM listings WHERE watch_id=? AND bucket=? AND last_seen>=? AND risk<?",
            (wid, bucket, since, max_risk),
        ).fetchall()
        return [r["total"] for r in rows]

    # --- alerts & snapshots -----------------------------------------------
    def add_alert(self, wid: int, key: str, reason: str, title: str, total: float, url: str) -> None:
        with self.lock, self.conn:
            self.conn.execute(
                "INSERT INTO alerts (watch_id, key, reason, title, total, url, created_at) VALUES (?,?,?,?,?,?,?)",
                (wid, key, reason, title, total, url, utcnow().isoformat()),
            )

    def list_alerts(self, since_id: int = 0, limit: int = 50) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM alerts WHERE id>? ORDER BY id DESC LIMIT ?", (since_id, limit)
        ).fetchall()
        return [dict(r) for r in rows]

    def add_snapshot(self, wid: int, ts: str, bucket: str, min_total: float, median_total: float, count: int) -> None:
        with self.lock, self.conn:
            self.conn.execute(
                "INSERT INTO snapshots VALUES (?,?,?,?,?,?)", (wid, ts, bucket, min_total, median_total, count)
            )

    def snapshots(self, wid: int, since: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT ts, bucket, min_total, median_total, count FROM snapshots WHERE watch_id=? AND ts>=? ORDER BY ts",
            (wid, since),
        ).fetchall()
        return [dict(r) for r in rows]
