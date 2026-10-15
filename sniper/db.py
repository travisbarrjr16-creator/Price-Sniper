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
CREATE TABLE IF NOT EXISTS user_hidden (
    user_id INTEGER NOT NULL,
    kind TEXT NOT NULL,           -- 'seller' (site:seller name) or 'listing' (listing key)
    value TEXT NOT NULL,
    label TEXT,                   -- what to show in the UI
    created_at TEXT NOT NULL,
    PRIMARY KEY (user_id, kind, value)
);
CREATE TABLE IF NOT EXISTS password_resets (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS api_usage (
    day TEXT NOT NULL,            -- UTC date, e.g. 2026-10-15
    source TEXT NOT NULL,
    calls INTEGER NOT NULL,
    PRIMARY KEY (day, source)
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
            # columns added after the first release
            for table, col, decl in [
                ("watches", "user_id", "INTEGER"),
                ("users", "quiet_start", "TEXT"), ("users", "quiet_end", "TEXT"), ("users", "quiet_tz", "TEXT"),
                ("users", "quiet_allow_urgent", "INTEGER NOT NULL DEFAULT 1"),
            ]:
                cols = {r["name"] for r in self.conn.execute(f"PRAGMA table_info({table})")}
                if col not in cols:
                    self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
            self.conn.commit()

    # --- locked reads: the connection is shared by the API's worker threads ---
    def _read(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self.lock:
            return self.conn.execute(sql, params).fetchall()

    def _read_one(self, sql: str, params: tuple = ()) -> Optional[sqlite3.Row]:
        rows = self._read(sql, params)
        return rows[0] if rows else None

    # --- shared API usage (all users share the server's marketplace keys) ----
    def add_usage(self, source: str, calls: int) -> None:
        if calls <= 0:
            return
        day = utcnow().date().isoformat()
        with self.lock, self.conn:
            self.conn.execute(
                "INSERT INTO api_usage VALUES (?, ?, ?) ON CONFLICT(day, source) DO UPDATE SET calls = calls + excluded.calls",
                (day, source, calls))

    def usage_today(self, source: str) -> int:
        r = self._read_one("SELECT calls FROM api_usage WHERE day=? AND source=?", (utcnow().date().isoformat(), source))
        return r["calls"] if r else 0

    # --- app-wide settings (e.g. the maintenance notice) -------------------
    def get_setting(self, key: str) -> Optional[str]:
        r = self._read_one("SELECT value FROM app_settings WHERE key=?", (key,))
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
                    discord_webhook=r["discord_webhook"], gmail_email=r["gmail_email"],
                    quiet_start=r["quiet_start"], quiet_end=r["quiet_end"], quiet_tz=r["quiet_tz"],
                    quiet_allow_urgent=bool(r["quiet_allow_urgent"]))

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
        r = self._read_one("SELECT * FROM users WHERE id=?", (uid,))
        return self._row_to_user(r) if r else None

    def authenticate(self, email: str, password: str) -> Optional[User]:
        r = self._read_one("SELECT * FROM users WHERE email=?", (email.strip(),))
        if r and security.verify_password(password, r["pw_hash"]):
            return self._row_to_user(r)
        security.verify_password(password, security.hash_password("timing-equalizer"))
        return None

    def update_user_settings(self, uid: int, ntfy_topic: Optional[str], discord_webhook: Optional[str]) -> User:
        with self.lock, self.conn:
            self.conn.execute("UPDATE users SET ntfy_topic=?, discord_webhook=? WHERE id=?",
                              (ntfy_topic, discord_webhook, uid))
        return self.get_user(uid)

    def update_quiet_hours(self, uid: int, start: Optional[str], end: Optional[str],
                           tz: Optional[str], allow_urgent: bool) -> User:
        with self.lock, self.conn:
            self.conn.execute(
                "UPDATE users SET quiet_start=?, quiet_end=?, quiet_tz=?, quiet_allow_urgent=? WHERE id=?",
                (start, end, tz, int(allow_urgent), uid))
        return self.get_user(uid)

    def find_user_by_email(self, email: str) -> Optional[User]:
        r = self._read_one("SELECT * FROM users WHERE email=?", (email.strip(),))
        return self._row_to_user(r) if r else None

    def set_password(self, uid: int, password: str) -> None:
        """Also signs the user out everywhere."""
        with self.lock, self.conn:
            self.conn.execute("UPDATE users SET pw_hash=? WHERE id=?", (security.hash_password(password), uid))
            self.conn.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
            self.conn.execute("DELETE FROM password_resets WHERE user_id=?", (uid,))

    def create_reset_token(self, uid: int, hours: int = 24) -> str:
        token = security.new_token()
        exp = (utcnow() + timedelta(hours=hours)).isoformat()
        with self.lock, self.conn:
            self.conn.execute("INSERT INTO password_resets VALUES (?, ?, ?)", (security.token_digest(token), uid, exp))
        return token

    def use_reset_token(self, token: str, new_password: str) -> Optional[User]:
        r = self._read_one("SELECT user_id, expires_at FROM password_resets WHERE token_hash=?",
                           (security.token_digest(token),))
        if not r or r["expires_at"] < utcnow().isoformat():
            return None
        self.set_password(r["user_id"], new_password)   # also deletes this (and every) reset token
        return self.get_user(r["user_id"])

    # --- per-user hidden sellers / dismissed listings ------------------------
    def hide(self, uid: int, kind: str, value: str, label: Optional[str]) -> None:
        with self.lock, self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO user_hidden VALUES (?, ?, ?, ?, ?)",
                (uid, kind, value, label, utcnow().isoformat()))

    def unhide(self, uid: int, kind: str, value: str) -> None:
        with self.lock, self.conn:
            self.conn.execute("DELETE FROM user_hidden WHERE user_id=? AND kind=? AND value=?", (uid, kind, value))

    def list_hidden(self, uid: int) -> list[dict]:
        return [dict(r) for r in self._read(
            "SELECT kind, value, label, created_at FROM user_hidden WHERE user_id=? ORDER BY created_at DESC", (uid,))]

    def hidden_sets(self, uid: Optional[int]) -> tuple[set[str], set[str]]:
        """(muted seller keys, dismissed listing keys)"""
        if uid is None:
            return set(), set()
        sellers, listings = set(), set()
        for r in self._read("SELECT kind, value FROM user_hidden WHERE user_id=?", (uid,)):
            (sellers if r["kind"] == "seller" else listings).add(r["value"])
        return sellers, listings

    def set_gmail(self, uid: int, email: Optional[str], refresh_token: Optional[str]) -> None:
        enc = security.encrypt(refresh_token) if refresh_token else None
        with self.lock, self.conn:
            self.conn.execute("UPDATE users SET gmail_email=?, gmail_token=? WHERE id=?", (email, enc, uid))

    def record_push_result(self, uid: int, results: dict) -> None:
        """Remember the latest phone/Discord delivery problem so Settings can show it (cleared on success)."""
        failed = {k: v for k, v in results.items() if v}
        self.set_setting(f"push_error:{uid}", json.dumps({"at": utcnow().isoformat(), "errors": failed}) if failed else None)

    def push_error(self, uid: int) -> Optional[dict]:
        raw = self.get_setting(f"push_error:{uid}")
        return json.loads(raw) if raw else None

    def expire_gmail(self, uid: int) -> None:
        """Keep the address (so the app can say "reconnect name@gmail.com") but drop the dead token."""
        with self.lock, self.conn:
            self.conn.execute("UPDATE users SET gmail_token=NULL WHERE id=?", (uid,))

    def gmail_connected(self, uid: int) -> bool:
        r = self._read_one("SELECT gmail_token FROM users WHERE id=?", (uid,))
        return bool(r and r["gmail_token"])

    def get_gmail_token(self, uid: int) -> Optional[str]:
        r = self._read_one("SELECT gmail_token FROM users WHERE id=?", (uid,))
        return security.decrypt(r["gmail_token"]) if r and r["gmail_token"] else None

    def create_session(self, uid: int, days: int = 30) -> str:
        token = security.new_token()
        exp = (utcnow() + timedelta(days=days)).isoformat()
        with self.lock, self.conn:
            self.conn.execute("INSERT INTO sessions VALUES (?, ?, ?)", (security.token_digest(token), uid, exp))
        return token

    def session_user(self, token: str) -> Optional[User]:
        r = self._read_one("SELECT user_id, expires_at FROM sessions WHERE token_hash=?",
                           (security.token_digest(token),))
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
        r = self._read_one("SELECT * FROM watches WHERE id=?", (wid,))
        return self._row_to_watch(r) if r else None

    def list_watches(self, user_id: Optional[int] = None) -> list[Watch]:
        """All watches (for the background loop), or one user's."""
        if user_id is None:
            rows = self._read("SELECT * FROM watches ORDER BY id")
        else:
            rows = self._read("SELECT * FROM watches WHERE user_id=? ORDER BY id", (user_id,))
        return [self._row_to_watch(r) for r in rows]

    def delete_watch(self, wid: int) -> None:
        with self.lock, self.conn:
            for t in ("watches",):
                self.conn.execute(f"DELETE FROM {t} WHERE id=?", (wid,))
            for t in ("listings", "alerts", "snapshots"):
                self.conn.execute(f"DELETE FROM {t} WHERE watch_id=?", (wid,))

    def delete_user(self, uid: int) -> None:
        """Remove an account and everything that belongs to it."""
        for w in self.list_watches(uid):
            self.delete_watch(w.id)
        with self.lock, self.conn:
            for t in ("sessions", "password_resets", "user_hidden"):
                self.conn.execute(f"DELETE FROM {t} WHERE user_id=?", (uid,))
            self.conn.execute("DELETE FROM users WHERE id=?", (uid,))
            self.conn.execute("DELETE FROM app_settings WHERE key=?", (f"push_error:{uid}",))

    # --- listings ----------------------------------------------------------
    def get_listing_rows(self, wid: int, active_only: bool = True) -> list[sqlite3.Row]:
        q = "SELECT * FROM listings WHERE watch_id=?" + (" AND active=1" if active_only else "")
        return self._read(q, (wid,))

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

    def lowest_seen(self, wid: int, max_risk: int) -> Optional[float]:
        r = self._read_one("SELECT MIN(total) AS m FROM listings WHERE watch_id=? AND risk<?", (wid, max_risk))
        return r["m"] if r else None

    def reference_totals(self, wid: int, bucket: str, since: str, max_risk: int) -> list[float]:
        """Totals of every distinct, non-risky listing seen recently (active or gone)."""
        rows = self._read(
            "SELECT total FROM listings WHERE watch_id=? AND bucket=? AND last_seen>=? AND risk<?",
            (wid, bucket, since, max_risk),
        )
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
            rows = self._read(
                "SELECT * FROM alerts WHERE id>? ORDER BY id DESC LIMIT ?", (since_id, limit)
            )
        else:
            rows = self._read(
                """SELECT a.*, json_extract(w.data, '$.query') AS query FROM alerts a JOIN watches w ON w.id = a.watch_id
                   WHERE w.user_id=? AND a.id>? ORDER BY a.id DESC LIMIT ?""", (user_id, since_id, limit)
            )
        return [dict(r) for r in rows]

    def add_snapshot(self, wid: int, ts: str, bucket: str, min_total: float, median_total: float, count: int) -> None:
        with self.lock, self.conn:
            self.conn.execute(
                "INSERT INTO snapshots VALUES (?,?,?,?,?,?)", (wid, ts, bucket, min_total, median_total, count)
            )

    def snapshots(self, wid: int, since: str) -> list[dict]:
        rows = self._read(
            "SELECT ts, bucket, min_total, median_total, count FROM snapshots WHERE watch_id=? AND ts>=? ORDER BY ts",
            (wid, since),
        )
        return [dict(r) for r in rows]
