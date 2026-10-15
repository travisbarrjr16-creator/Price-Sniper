"""The background loop: poll every due watch, score results, persist, alert."""
from __future__ import annotations

import asyncio
import logging
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timedelta

import httpx

from . import budget, scoring
from .alerts import Notifier
from .db import DB
from .matching import match
from .models import Listing, ScoredListing, Watch, utcnow
from .sources import Source, enabled_sources, seller_key

log = logging.getLogger("sniper.watcher")

HISTORY_DAYS = 30


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
    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()

    async def _loop(self) -> None:
        while True:
            try:
                for w in self.db.list_watches():
                    if w.active and self._due(w):
                        await self.check(w)
            except Exception:
                log.exception("watch loop error")
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

            for s, reason, urgent in alerts:
                await self._send_alert(client, watch, s, reason, urgent)

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
                return await source.search(watch, client)
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
        for s in sorted(visible, key=lambda s: s.total_cost):
            prev = existing.get(s.listing.key)
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

    async def _send_alert(self, client, watch: Watch, s: ScoredListing, reason: str, urgent: bool = False) -> None:
        l = s.listing
        where = f"{l.distance_miles:g} mi away" if l.local_pickup and l.distance_miles is not None else l.source
        deal = f" · {s.deal['pct_vs_median']:+.0f}% vs typical" if s.deal else ""
        title = f"${s.total_cost:.2f} · {watch.query}"
        msg = f"{reason}\n{l.title}\n{l.condition.replace('_', ' ')} · {where}{deal}"
        self.db.add_alert(watch.id, l.key, reason, l.title, s.total_cost, l.url)
        self.db.set_alerted(watch.id, l.key, s.total_cost)
        user = self.db.get_user(watch.user_id) if watch.user_id else None
        if user and user.in_quiet_hours(utcnow()) and not (urgent and user.quiet_allow_urgent):
            log.info("quiet hours for user %s: alert kept in app, no push", user.id)
            return
        await self.notifier.send(
            client, title, msg, l.url, urgent=urgent,
            ntfy_topic=user.ntfy_topic if user else None,
            discord_webhook=user.discord_webhook if user else None,
        )
