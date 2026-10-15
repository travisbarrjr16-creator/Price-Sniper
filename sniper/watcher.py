"""The background loop: poll every due watch, score results, persist, alert."""
from __future__ import annotations

import asyncio
import json
import os
import logging
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timedelta

import httpx

from . import budget, housekeeping, scoring
from .alerts import Notifier
from .db import DB
from .matching import match
from .models import Listing, ScoredListing, Watch, utcnow
from .matching import normalize
from .sources import Source, enabled_sources, seller_key, site_of

log = logging.getLogger("sniper.watcher")

HISTORY_DAYS = 30
SITE_TIME_LIMIT = 60          # seconds one site's whole search may take before we move on
RELIST_DAYS = 7               # same seller + same title + similar price within this = the same item


def _fingerprint(source: str, seller: str | None, title: str) -> tuple | None:
    return (site_of(source), seller.strip().lower(), normalize(title)) if seller else None


def scored_to_dict(s: ScoredListing) -> dict:
    d = asdict(s.listing)
    if d.get("listed_at"):
        d["listed_at"] = d["listed_at"].isoformat()
    d.update(
        key=s.listing.key,
        total_cost=s.total_cost,
        shipping_estimated=s.shipping_estimated,
        tax=s.tax,
        effective_cost=s.effective_cost,
        risk_score=s.risk_score,
        risk_flags=s.risk_flags,
        deal=s.deal,
    )
    return d


class Watcher:
    def __init__(self, db: DB, notifier: Notifier | None = None, sources: list[Source] | None = None,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.db = db
        self._transport = transport       # tests swap in a fake network
        self.notifier = notifier or Notifier()
        self._sources = sources
        self.last_stats: dict[int, dict] = {}
        self._task: asyncio.Task | None = None
        self._locks: dict[int, asyncio.Lock] = {}

    @property
    def sources(self) -> list[Source]:
        return self._sources if self._sources is not None else enabled_sources()

    # --- loop --------------------------------------------------------------
    def start(self) -> bool:
        """Start the background checks, unless another copy of the app already runs them on this database
        (two copies would check twice and send every alert twice)."""
        if not self._take_lock():
            log.warning("another Price Sniper process is already running the watches for %s; "
                        "this one will only serve the web page", self.db.path)
            return False
        self._task = asyncio.create_task(self._loop())
        return True

    def _take_lock(self) -> bool:
        if self.db.path == ":memory:":
            return True
        self._lock_file = open(f"{self.db.path}.watcher.lock", "a+")
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self._lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True          # held until this process exits (the OS releases it even after a crash)
        except OSError:
            self._lock_file.close()
            return False

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()

    async def _loop(self) -> None:
        last_housekeeping = None
        while True:
            today = utcnow().date()
            if os.getenv("SNIPER_HOUSEKEEPING", "1") != "0" and last_housekeeping != today:
                last_housekeeping = today
                await asyncio.to_thread(housekeeping.daily, self.db)
            try:
                watches = self.db.list_watches()
            except Exception:
                log.exception("could not load watches")
                watches = []
            for w in watches:
                if not (w.active and self._due(w)):
                    continue
                try:
                    await self.check(w)
                except Exception as e:
                    # One broken watch must not block the others, or retry every 20s and burn API quota:
                    # record the error and wait for its normal interval.
                    log.exception("watch %s failed", w.id)
                    w.last_checked_at = utcnow().isoformat()
                    w.last_error = f"Check failed: {type(e).__name__}: {e}"[:300]
                    try:
                        self.db.update_watch(w)
                    except Exception:
                        log.exception("could not record failure for watch %s", w.id)
            await asyncio.sleep(20)

    @staticmethod
    def _due(w: Watch) -> bool:
        if not w.last_checked_at:
            return True
        last = datetime.fromisoformat(w.last_checked_at)
        return utcnow() - last >= timedelta(minutes=w.interval_minutes)

    # --- one check ---------------------------------------------------------
    async def check(self, watch: Watch) -> dict:
        lock = self._locks.setdefault(watch.id, asyncio.Lock())
        async with lock:
            return await self._check(watch)

    async def _check(self, watch: Watch) -> dict:
        sources = []
        errors: list[str] = []
        for s in budget.sources_for(watch, self.sources):
            if why := budget.can_run(self.db, s, watch):
                errors.append(why)          # daily limit reached: skip, don't fail
            else:
                sources.append(s)
        raw: list[Listing] = []
        async with httpx.AsyncClient(timeout=20, transport=self._transport) as client:
            results = await asyncio.gather(*(self._search_counted(s, watch) for s in sources), return_exceptions=True)
            for s, res in zip(sources, results):
                if isinstance(res, Exception):
                    errors.append(f"{s.name}: {res}")
                    log.warning("source %s failed: %s", s.name, res)
                else:
                    for l in res:
                        l.source_key = s.key
                    raw.extend(res)

            # Strict matching
            rejected = Counter()
            matched: dict[str, Listing] = {}
            for l in raw:
                ok, why = match(l, watch)
                if ok:
                    matched[l.key] = l
                else:
                    rejected[why.split(" ")[0]] += 1

            # Reference medians: recent history merged with this run (current prices win)
            now = utcnow()
            since = (now - timedelta(days=HISTORY_DAYS)).isoformat()
            existing = self.db.existing_listings(watch.id)
            medians: dict[str, tuple[float | None, int]] = {}
            for bucket in ("new", "used"):
                ref: dict[str, float] = {
                    r["key"]: r["total"] for r in existing.values()
                    if r["bucket"] == bucket and r["last_seen"] >= since and r["risk"] < scoring.RISK_HIDE_THRESHOLD
                }
                for k, l in matched.items():
                    if scoring.condition_bucket(l.condition) == bucket:
                        ref[k] = scoring.total_cost(l, watch)[0]
                medians[bucket] = scoring.median_of(ref.values())

            scored = [scoring.score(l, watch, medians) for l in matched.values()]
            visible = [s for s in scored if not (watch.hide_risky and s.risk_score >= scoring.RISK_HIDE_THRESHOLD)]

            # The user's muted sellers and "not interested" listings never alert.
            muted, dismissed = self.db.hidden_sets(watch.user_id)
            alertable = [s for s in visible if s.listing.key not in dismissed
                         and seller_key(s.listing.source, s.listing.seller_name) not in muted]

            # Alerts — computed against state *before* this run is saved
            prev_active = [r for r in existing.values() if r["active"] and r["risk"] < scoring.RISK_HIDE_THRESHOLD]
            prev_lowest = min((r["total"] for r in prev_active), default=None)
            first_run = not existing
            alerts = self._pick_alerts(watch, alertable, existing, prev_lowest, first_run)

            ts = now.isoformat()
            for s in scored:
                self.db.upsert_listing(
                    watch.id, s.listing.key, scored_to_dict(s), s.total_cost, s.effective_cost,
                    scoring.condition_bucket(s.listing.condition), s.risk_score, ts,
                )
            self.db.mark_inactive_except(watch.id, set(matched))

            for bucket in ("new", "used"):
                totals = [s.total_cost for s in visible if scoring.condition_bucket(s.listing.condition) == bucket]
                if totals:
                    med, n = scoring.median_of(totals)
                    self.db.add_snapshot(watch.id, ts, bucket, min(totals), med, n)

            if alerts:
                await self._send_alerts(client, watch, alerts)

        watch.last_checked_at = ts
        watch.last_error = "; ".join(errors) or None
        self.db.update_watch(watch)
        stats = {
            "checked_at": ts,
            "sources": [s.name for s in sources],
            "fetched": len(raw),
            "matched": len(matched),
            "visible": len(visible),
            "hidden_risky": len(scored) - len(visible),
            "rejected": dict(rejected),
            "alerts": len(alerts),
            "errors": errors,
        }
        self.last_stats[watch.id] = stats
        return stats

    async def _search_counted(self, source: Source, watch: Watch) -> list[Listing]:
        """Run one source's search on its own client and record every request it sends."""
        calls = 0

        async def count(_request):
            nonlocal calls
            calls += 1

        try:
            async with httpx.AsyncClient(timeout=20, transport=self._transport,
                                         event_hooks={"request": [count]}) as client:
                # a site that answers very slowly mustn't hold up everyone's checks
                return await asyncio.wait_for(source.search(watch, client), SITE_TIME_LIMIT)
        except asyncio.TimeoutError:
            raise RuntimeError(f"took longer than {SITE_TIME_LIMIT}s, skipped this check") from None
        finally:
            self.db.add_usage(source.key, calls)

    def _pick_alerts(self, watch, visible, existing, prev_lowest, first_run) -> list[tuple[ScoredListing, str, bool]]:
        """Returns (listing, reason, urgent). Kinds, most important first:
        🎯 target hit (urgent) · 🔥 new lowest (urgent) · ⬇ price drop under max · 🆕 new under max."""
        if not visible:
            return []
        under = [s for s in visible if watch.max_price and s.total_cost <= watch.max_price]
        on_target = [s for s in visible if watch.target_price and s.total_cost <= watch.target_price]
        if first_run:
            # Don't spam on the first scan: one summary alert for the cheapest hit.
            if on_target:
                best = min(on_target, key=lambda s: s.total_cost)
                return [(best, f"🎯 {len(on_target)} listing(s) already at your ${watch.target_price:.0f} target", True)]
            if under:
                best = min(under, key=lambda s: s.total_cost)
                return [(best, f"🆕 {len(under)} listing(s) already under your ${watch.max_price:.0f} max", False)]
            return []

        out = []
        relists = self._relist_index(existing)
        for s in sorted(visible, key=lambda s: s.total_cost):
            prev = existing.get(s.listing.key)
            if prev is None:
                # Ended and relisted under a new id? Treat it as the item we already know about.
                old = relists.get(_fingerprint(s.listing.source, s.listing.seller_name, s.listing.title))
                if old is not None and abs(s.total_cost - old["total"]) <= max(5.0, old["total"] * 0.05):
                    prev = old
            is_new = prev is None
            alerted = prev["alerted_total"] if prev is not None else None
            dropped = prev is not None and s.total_cost < (alerted or prev["total"]) - 1
            if not (is_new or dropped):
                continue
            beats = prev_lowest is not None and s.total_cost < prev_lowest
            great = bool(s.deal and s.deal["label"] == "great")
            if s in on_target:
                out.append((s, f"🎯 hit your ${watch.target_price:.0f} target", True))
            elif beats:
                out.append((s, f"🔥 new lowest price (was ${prev_lowest:.2f})", True))
            elif s in under and (is_new or (alerted is None or s.total_cost < alerted - 1)):
                out.append((s, "🆕 new listing under your max" if is_new else "⬇ price drop under your max", great))
            else:
                continue
            if beats:
                prev_lowest = s.total_cost
        return out[:5]

    @staticmethod
    def _relist_index(existing: dict) -> dict:
        cutoff = (utcnow() - timedelta(days=RELIST_DAYS)).isoformat()
        index = {}
        for r in existing.values():
            if r["last_seen"] < cutoff:
                continue
            d = json.loads(r["data"])
            if d.get("seller_name"):
                index[_fingerprint(d["source"], d["seller_name"], d["title"])] = r
        return index

    async def _send_alerts(self, client, watch: Watch, alerts: list[tuple[ScoredListing, str, bool]]) -> None:
        """Every alert is saved for the app; the phone gets ONE notification per check, led by the most
        important deal (target hits and new lows first), so several finds don't mean several buzzes."""
        for s, reason, _ in alerts:
            self.db.add_alert(watch.id, s.listing.key, reason, s.listing.title, s.total_cost, s.listing.url)
            self.db.set_alerted(watch.id, s.listing.key, s.total_cost)

        ranked = sorted(alerts, key=lambda a: (not a[2], a[0].total_cost))   # urgent first, then cheapest
        top, reason, _ = ranked[0]
        urgent = any(u for _, _, u in alerts)
        l = top.listing
        where = f"{l.distance_miles:g} mi away" if l.local_pickup and l.distance_miles is not None else l.source
        deal = f" · {top.deal['pct_vs_median']:+.0f}% vs typical" if top.deal else ""
        if len(ranked) == 1:
            title = f"${top.total_cost:.2f} · {watch.query}"
            msg = f"{reason}\n{l.title}\n{l.condition.replace('_', ' ')} · {where}{deal}"
        else:
            title = f"{len(ranked)} deals · {watch.query} · from ${min(a[0].total_cost for a in ranked):.2f}"
            others = [f"• ${a[0].total_cost:.2f} {a[1]}" for a in ranked[1:4]]
            more = f"\n+{len(ranked) - 4} more in the app" if len(ranked) > 4 else ""
            msg = f"{reason}: ${top.total_cost:.2f} {l.title} ({where}{deal})\n" + "\n".join(others) + more

        user = self.db.get_user(watch.user_id) if watch.user_id else None
        if user and user.in_quiet_hours(utcnow()) and not (urgent and user.quiet_allow_urgent):
            log.info("quiet hours for user %s: %d alert(s) kept in app, no push", user.id, len(ranked))
            return
        await self.notifier.send(
            client, title, msg, l.url, urgent=urgent,       # tapping opens the top deal
            ntfy_topic=user.ntfy_topic if user else None,
            discord_webhook=user.discord_webhook if user else None,
        )
