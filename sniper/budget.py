"""Keep everyone's watches inside each marketplace key's daily request limit.

Two guards:
- Planning: a new/edited watch is refused if, together with every other active
  watch, it would need more requests per day than the limit allows.
- Running: if today's real usage gets close to the limit, that site is skipped
  until midnight UTC, with a message instead of a failed request.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from math import ceil
from typing import Iterable, Optional

from .db import DB
from .models import Watch
from .sources import Source

RESERVE = 0.9          # plan for 90% of the limit; the rest covers "Check now", retries and logins
NICE_INTERVALS = [15, 20, 30, 45, 60, 90, 120, 180, 240, 360, 720, 1440]


def sources_for(watch: Watch, enabled: Iterable[Source]) -> list[Source]:
    return [s for s in enabled if not watch.sources or s.key in watch.sources]


def daily_need(watch: Watch, source: Source) -> float:
    return (1440 / max(watch.interval_minutes, 1)) * source.calls_per_check(watch)


def projected(db: DB, enabled: list[Source], skip_id: Optional[int] = None) -> dict[str, float]:
    """Requests per day all active watches will need, per source."""
    out = {s.key: 0.0 for s in enabled}
    for w in db.list_watches():
        if not w.active or w.id == skip_id:
            continue
        for s in sources_for(w, enabled):
            out[s.key] += daily_need(w, s)
    return out


def check_watch(db: DB, watch: Watch, enabled: list[Source]) -> Optional[str]:
    """None if the watch fits, otherwise a message saying why and what would fit."""
    if not watch.active:
        return None
    plan = projected(db, enabled, skip_id=watch.id)
    for s in sources_for(watch, enabled):
        limit = s.daily_limit()
        if not limit:
            continue
        budget = limit * RESERVE
        others = plan[s.key]
        if others + daily_need(watch, s) <= budget:
            continue
        room = budget - others
        fits = next((m for m in NICE_INTERVALS if room > 0 and (1440 / m) * s.calls_per_check(watch) <= room), None)
        msg = (f"{s.name}'s daily search limit is shared by everyone on this app, and active watches already "
               f"need about {int(others):,} of the {int(budget):,} searches per day it allows.")
        if fits and fits > watch.interval_minutes:
            return msg + f" Set this watch to check every {fits} minutes or more, or pause another watch."
        return msg + f" Pause or slow down another watch, or untick {s.name} for this one."
    return None


def can_run(db: DB, source: Source, watch: Watch) -> Optional[str]:
    """None if this check may use the source now, otherwise why not."""
    limit = source.daily_limit()
    if not limit:
        return None
    used = db.usage_today(source.key)
    if used + source.calls_per_check(watch) <= limit * 0.98:
        return None
    now = datetime.now(timezone.utc)
    midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    hours = ceil((midnight - now).total_seconds() / 3600)
    return (f"{source.name}: paused for today, the shared daily limit is used up ({used:,} of {limit:,}). "
            f"Back in about {hours} hour{'s' if hours != 1 else ''}.")


def report(db: DB, enabled: list[Source]) -> list[dict]:
    plan = projected(db, enabled)
    return [{"key": s.key, "name": s.name, "limit": s.daily_limit(), "used_today": db.usage_today(s.key),
             "planned_per_day": int(plan[s.key])} for s in enabled if s.daily_limit()]
