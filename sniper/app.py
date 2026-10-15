"""HTTP API + web UI. Run with:  uvicorn sniper.app:app --port 8000"""
from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import scoring
from .db import DB
from .models import CONDITIONS, Watch, utcnow
from .sources import ALL_SOURCES, enabled_sources
from .watcher import HISTORY_DAYS, Watcher

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
STATIC = Path(__file__).parent / "static"


class WatchIn(BaseModel):
    query: str = Field(min_length=2)
    max_price: Optional[float] = None
    conditions: list[str] = ["new", "open_box", "refurbished", "used"]
    zip_code: Optional[str] = None
    radius_miles: Optional[int] = None
    required_terms: list[str] = []
    excluded_terms: list[str] = []
    sources: list[str] = []
    tax_rate: float = 0.0
    interval_minutes: int = Field(15, ge=1)
    strict_variants: bool = True
    hide_risky: bool = True
    include_auctions: bool = False
    active: bool = True


def build_app(db: DB | None = None, watcher: Watcher | None = None, background: bool = True) -> FastAPI:
    db = db or DB()
    watcher = watcher or Watcher(db)

    @asynccontextmanager
    async def lifespan(_app):
        if background:
            watcher.start()
        yield
        await watcher.stop()

    app = FastAPI(title="Price Sniper", lifespan=lifespan)
    app.state.db, app.state.watcher = db, watcher

    def _get(wid: int) -> Watch:
        w = db.get_watch(wid)
        if not w:
            raise HTTPException(404, "watch not found")
        return w

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/sources")
    def sources():
        on = {s.key for s in enabled_sources()}
        return [{"key": s.key, "name": s.name, "enabled": s.key in on} for s in ALL_SOURCES]

    @app.get("/api/watches")
    def list_watches():
        out = []
        for w in db.list_watches():
            rows = [r for r in db.get_listing_rows(w.id) if r["risk"] < scoring.RISK_HIDE_THRESHOLD or not w.hide_risky]
            best = min(rows, key=lambda r: r["total"], default=None)
            out.append({**w.to_dict(), "count": len(rows), "lowest": best["total"] if best else None,
                        "stats": watcher.last_stats.get(w.id)})
        return out

    @app.post("/api/watches")
    async def create_watch(body: WatchIn, check_now: bool = True):
        bad = [c for c in body.conditions if c not in CONDITIONS]
        if bad:
            raise HTTPException(422, f"unknown condition(s): {bad}")
        w = db.create_watch(Watch(**body.model_dump()))
        if check_now:
            await watcher.check(w)
        return db.get_watch(w.id).to_dict()

    @app.put("/api/watches/{wid}")
    def update_watch(wid: int, body: WatchIn):
        w = _get(wid)
        for k, v in body.model_dump().items():
            setattr(w, k, v)
        return db.update_watch(w).to_dict()

    @app.delete("/api/watches/{wid}")
    def delete_watch(wid: int):
        _get(wid)
        db.delete_watch(wid)
        return {"ok": True}

    @app.post("/api/watches/{wid}/check")
    async def check_now(wid: int):
        return await watcher.check(_get(wid))

    @app.get("/api/watches/{wid}/listings")
    def listings(wid: int, sort: str = "total", include_hidden: bool = False):
        w = _get(wid)
        items = []
        for r in db.get_listing_rows(wid):
            d = json.loads(r["data"])
            d["first_seen"], d["first_total"] = r["first_seen"], r["first_price"]
            d["hidden"] = w.hide_risky and r["risk"] >= scoring.RISK_HIDE_THRESHOLD
            if d["hidden"] and not include_hidden:
                continue
            items.append(d)
        keyfn = {
            "total": lambda d: d["total_cost"],
            "value": lambda d: d["effective_cost"],
            "distance": lambda d: (d["distance_miles"] is None, d["distance_miles"] or 0),
            "newest": lambda d: d["first_seen"],
        }.get(sort)
        if not keyfn:
            raise HTTPException(422, "sort must be total|value|distance|newest")
        items.sort(key=keyfn, reverse=(sort == "newest"))
        return items

    @app.get("/api/watches/{wid}/history")
    def history(wid: int, days: int = HISTORY_DAYS):
        _get(wid)
        since = (utcnow() - timedelta(days=days)).isoformat()
        out = {}
        for b in ("new", "used"):
            med, n = scoring.median_of(db.reference_totals(wid, b, since, scoring.RISK_HIDE_THRESHOLD))
            out[b] = {"typical": med, "sample_size": n}
        return {"snapshots": db.snapshots(wid, since), "reference": out}

    @app.get("/api/alerts")
    def alerts(since_id: int = 0):
        return db.list_alerts(since_id)

    @app.get("/api/status")
    def status():
        return {"sources": [s.name for s in enabled_sources()], "alert_channels": watcher.notifier.channels()}

    return app


app = build_app()
