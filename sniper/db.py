"""SQLite persistence: watches, every listing seen, alerts, price snapshots."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import timedelta
from typing import Optional

from . import security
from .models import User, Watch, utcnow

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
    pw_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    ntfy_topic TEXT,
    discord_webhook TEXT,
    gmail_email TEXT,
    gmail_token TEXT              -- encrypted refresh token
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS watches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
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
CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
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
            cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(watches)")}
            if "user_id" not in cols:   # databases from before accounts existed
                self.conn.execute("ALTER TABLE watches ADD COLUMN user_id INTEGER")
            self.conn.commit()

    # --- app-wide settings (e.g. the maintenance notice) -------------------
    def get_setting(self, key: str) -> Optional[str]:
        r = self.conn.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
        return r["value"] if r else None

    def set_setting(self, key: str, value: Optional[str]) -> None:
        with self.lock, self.conn:
            if value is None:
                self.conn.execute("DELETE FROM app_settings WHERE key=?", (key,))
            else:
                self.conn.execute("INSERT INTO app_settings VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                                  (key, value))

    # --- users & sessions --------------------------------------------------
    def _row_to_user(self, r: sqlite3.Row) -> User:
        return User(id=r["id"], email=r["email"], ntfy_topic=r["ntfy_topic"],
                    discord_webhook=r["discord_webhook"], gmail_email=r["gmail_email"])

    def create_user(self, email: str, password: str) -> Optional[User]:
        try:
            with self.lock, self.conn:
                cur = self.conn.execute(
                    "INSERT INTO users (email, pw_hash, created_at) VALUES (?, ?, ?)",
                    (email.strip(), security.hash_password(password), utcnow().isoformat()),
                )
        except sqlite3.IntegrityError:
            return None
        return self.get_user(cur.lastrowid)

    def get_user(self, uid: int) -> Optional[User]:
        r = self.conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        return self._row_to_user(r) if r else None

    def authenticate(self, email: str, password: str) -> Optional[User]:
        r = self.conn.execute("SELECT * FROM users WHERE email=?", (email.strip(),)).fetchone()
        if r and security.verify_password(password, r["pw_hash"]):
            return self._row_to_user(r)
        security.verify_password(password, security.hash_password("timing-equalizer"))
        return None

    def update_user_settings(self, uid: int, ntfy_topic: Optional[str], discord_webhook: Optional[str]) -> User:
        with self.lock, self.conn:
            self.conn.execute("UPDATE users SET ntfy_topic=?, discord_webhook=? WHERE id=?",
                              (ntfy_topic, discord_webhook, uid))
        return self.get_user(uid)

    def set_gmail(self, uid: int, email: Optional[str], refresh_token: Optional[str]) -> None:
        enc = security.encrypt(refresh_token) if refresh_token else None
        with self.lock, self.conn:
            self.conn.execute("UPDATE users SET gmail_email=?, gmail_token=? WHERE id=?", (email, enc, uid))

    def get_gmail_token(self, uid: int) -> Optional[str]:
        r = self.conn.execute("SELECT gmail_token FROM users WHERE id=?", (uid,)).fetchone()
        return security.decrypt(r["gmail_token"]) if r and r["gmail_token"] else None

    def create_session(self, uid: int, days: int = 30) -> str:
        token = security.new_token()
        exp = (utcnow() + timedelta(days=days)).isoformat()
        with self.lock, self.conn:
            self.conn.execute("INSERT INTO sessions VALUES (?, ?, ?)", (security.token_digest(token), uid, exp))
        return token

    def session_user(self, token: str) -> Optional[User]:
        r = self.conn.execute("SELECT user_id, expires_at FROM sessions WHERE token_hash=?",
                              (security.token_digest(token),)).fetchone()
        if not r or r["expires_at"] < utcnow().isoformat():
            return None
        return self.get_user(r["user_id"])

    def delete_session(self, token: str) -> None:
        with self.lock, self.conn:
            self.conn.execute("DELETE FROM sessions WHERE token_hash=?", (security.token_digest(token),))

    # --- watches -----------------------------------------------------------
    _WATCH_META = ("id", "user_id", "active", "created_at", "last_checked_at", "last_error")

    def _row_to_watch(self, r: sqlite3.Row) -> Watch:
        d = json.loads(r["data"])
        d.update({k: r[k] for k in self._WATCH_META})
        d["active"] = bool(d["active"])
        return Watch(**d)

    def create_watch(self, w: Watch) -> Watch:
        data = {k: v for k, v in w.to_dict().items() if k not in self._WATCH_META}
        with self.lock, self.conn:
            cur = self.conn.execute(
                "INSERT INTO watches (user_id, data, active, created_at) VALUES (?, ?, ?, ?)",
                (w.user_id, json.dumps(data), int(w.active), utcnow().isoformat()),
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

    def list_watches(self, user_id: Optional[int] = None) -> list[Watch]:
        """All watches (for the background loop), or one user's."""
        if user_id is None:
            rows = self.conn.execute("SELECT * FROM watches ORDER BY id")
        else:
            rows = self.conn.execute("SELECT * FROM watches WHERE user_id=? ORDER BY id", (user_id,))
        return [self._row_to_watch(r) for r in rows]

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

    def list_alerts(self, user_id: Optional[int] = None, since_id: int = 0, limit: int = 50) -> list[dict]:
        if user_id is None:
            rows = self.conn.execute(
                "SELECT * FROM alerts WHERE id>? ORDER BY id DESC LIMIT ?", (since_id, limit)
            ).fetchall()
        else:
            rows = self.conn.execute(
                """SELECT a.* FROM alerts a JOIN watches w ON w.id = a.watch_id
                   WHERE w.user_id=? AND a.id>? ORDER BY a.id DESC LIMIT ?""", (user_id, since_id, limit)
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
