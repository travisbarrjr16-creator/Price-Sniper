"""HTTP API + web UI. Run with:  uvicorn sniper.app:app --port 8000"""
from __future__ import annotations

import json
import logging
import os
import secrets
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from typing import Optional

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, Field

from . import scoring
from .alerts import valid_discord_webhook, valid_ntfy_topic
from .db import DB
from .models import CONDITIONS, User, Watch, utcnow
from .sources import ALL_SOURCES, enabled_sources, get_source, site_of
from .sources import gmail
from .watcher import HISTORY_DAYS, Watcher

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
STATIC = Path(__file__).parent / "static"
COOKIE = "sniper_session"


class WatchIn(BaseModel):
    query: str = Field(min_length=2, max_length=120)
    max_price: Optional[float] = None
    conditions: list[str] = ["new", "open_box", "refurbished", "used"]
    zip_code: Optional[str] = Field(None, max_length=10)
    radius_miles: Optional[int] = None
    required_terms: list[str] = []
    excluded_terms: list[str] = []
    sources: list[str] = []
    tax_rate: float = Field(0.0, ge=0, le=0.25)
    interval_minutes: int = Field(15, ge=5)
    strict_variants: bool = True
    hide_risky: bool = True
    include_auctions: bool = False
    active: bool = True


class Credentials(BaseModel):
    email: str = Field(min_length=3, max_length=200)
    password: str = Field(min_length=8, max_length=200)
    invite_code: Optional[str] = None


class SettingsIn(BaseModel):
    ntfy_topic: Optional[str] = None
    discord_webhook: Optional[str] = None


def public_url() -> str:
    return os.getenv("PUBLIC_URL", "http://localhost:8000").rstrip("/")


def build_app(db: DB | None = None, watcher: Watcher | None = None, background: bool = True) -> FastAPI:
    db = db or DB()
    watcher = watcher or Watcher(db)
    gmail_source = get_source("gmail")
    if gmail_source:
        gmail_source.db = db
    oauth_states: dict[str, tuple[int, float]] = {}
    login_failures: dict[str, list[float]] = defaultdict(list)

    @asynccontextmanager
    async def lifespan(_app):
        if background:
            watcher.start()
        yield
        await watcher.stop()

    app = FastAPI(title="Price Sniper", lifespan=lifespan)
    app.state.db, app.state.watcher = db, watcher

    # --- auth ----------------------------------------------------------------
    def current_user(request: Request) -> User:
        token = request.cookies.get(COOKIE)
        user = db.session_user(token) if token else None
        if not user:
            raise HTTPException(401, "not signed in")
        return user

    def _start_session(resp: Response, user: User) -> None:
        resp.set_cookie(COOKIE, db.create_session(user.id), max_age=30 * 86400, httponly=True,
                        samesite="lax", secure=public_url().startswith("https://"))

    @app.post("/api/auth/signup")
    def signup(body: Credentials, response: Response):
        invite = os.getenv("SNIPER_INVITE_CODE")
        if invite and not secrets.compare_digest(body.invite_code or "", invite):
            raise HTTPException(403, "invite code required")
        if "@" not in body.email:
            raise HTTPException(422, "enter a valid email")
        user = db.create_user(body.email, body.password)
        if not user:
            raise HTTPException(409, "an account with that email already exists")
        _start_session(response, user)
        return user.public()

    @app.post("/api/auth/login")
    def login(body: Credentials, response: Response):
        key = body.email.strip().lower()
        recent = [t for t in login_failures[key] if time.time() - t < 900]
        login_failures[key] = recent
        if len(recent) >= 10:
            raise HTTPException(429, "too many attempts, try again in 15 minutes")
        user = db.authenticate(body.email, body.password)
        if not user:
            recent.append(time.time())
            raise HTTPException(401, "wrong email or password")
        login_failures.pop(key, None)
        _start_session(response, user)
        return user.public()

    @app.post("/api/auth/logout")
    def logout(request: Request, response: Response):
        if token := request.cookies.get(COOKIE):
            db.delete_session(token)
        response.delete_cookie(COOKIE)
        return {"ok": True}

    @app.get("/api/me")
    def me(user: User = Depends(current_user)):
        return {**user.public(), "gmail_available": bool(gmail_source and gmail_source.configured()),
                "invite_required": bool(os.getenv("SNIPER_INVITE_CODE"))}

    @app.put("/api/me")
    def update_me(body: SettingsIn, user: User = Depends(current_user)):
        topic = (body.ntfy_topic or "").strip() or None
        hook = (body.discord_webhook or "").strip() or None
        if not valid_ntfy_topic(topic):
            raise HTTPException(422, "ntfy topic: 6-64 letters, numbers, - or _")
        if not valid_discord_webhook(hook):
            raise HTTPException(422, "Discord webhook must be a https://discord.com/api/webhooks/... URL")
        return db.update_user_settings(user.id, topic, hook).public()

    @app.get("/api/auth/config")
    def auth_config():
        return {"invite_required": bool(os.getenv("SNIPER_INVITE_CODE"))}

    # --- Gmail connect (read-only OAuth) ------------------------------------
    redirect_uri = lambda: public_url() + "/api/gmail/callback"  # noqa: E731

    @app.get("/api/gmail/connect")
    def gmail_connect(user: User = Depends(current_user)):
        if not (gmail_source and gmail_source.configured()):
            raise HTTPException(400, "Gmail isn't set up on this server (GOOGLE_CLIENT_ID/SECRET)")
        state = secrets.token_urlsafe(24)
        oauth_states[state] = (user.id, time.time() + 600)
        return RedirectResponse(gmail.authorize_url(state, redirect_uri()))

    @app.get("/api/gmail/callback")
    async def gmail_callback(request: Request, state: str = "", code: str = "", error: str = ""):
        user = current_user(request)
        uid, exp = oauth_states.pop(state, (None, 0))
        if uid != user.id or time.time() > exp:
            raise HTTPException(400, "sign-in link expired, try connecting again")
        if error or not code:
            return RedirectResponse("/?gmail=cancelled")
        async with httpx.AsyncClient(timeout=20) as client:
            refresh, address = await gmail.exchange_code(client, code, redirect_uri())
        db.set_gmail(user.id, address, refresh)
        return RedirectResponse("/?gmail=connected")

    @app.post("/api/gmail/disconnect")
    def gmail_disconnect(user: User = Depends(current_user)):
        db.set_gmail(user.id, None, None)
        return {"ok": True}

    # --- watches ---------------------------------------------------------------
    def _get(wid: int, user: User) -> Watch:
        w = db.get_watch(wid)
        if not w or w.user_id != user.id:
            raise HTTPException(404, "watch not found")
        return w

    def _validate(body: WatchIn) -> None:
        bad = [c for c in body.conditions if c not in CONDITIONS]
        if bad:
            raise HTTPException(422, f"unknown condition(s): {bad}")
        known = {s.key for s in ALL_SOURCES}
        bad = [s for s in body.sources if s not in known]
        if bad:
            raise HTTPException(422, f"unknown source(s): {bad}")

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/sources")
    def sources():
        on = {s.key for s in enabled_sources()}
        return [{"key": s.key, "name": s.name, "enabled": s.key in on} for s in ALL_SOURCES]

    @app.get("/api/watches")
    def list_watches(user: User = Depends(current_user)):
        out = []
        for w in db.list_watches(user.id):
            rows = [r for r in db.get_listing_rows(w.id) if r["risk"] < scoring.RISK_HIDE_THRESHOLD or not w.hide_risky]
            best = min(rows, key=lambda r: r["total"], default=None)
            out.append({**w.to_dict(), "count": len(rows), "lowest": best["total"] if best else None,
                        "stats": watcher.last_stats.get(w.id)})
        return out

    @app.post("/api/watches")
    async def create_watch(body: WatchIn, check_now: bool = True, user: User = Depends(current_user)):
        _validate(body)
        if len(db.list_watches(user.id)) >= int(os.getenv("SNIPER_MAX_WATCHES", "25")):
            raise HTTPException(429, "watch limit reached; delete one first")
        w = db.create_watch(Watch(**body.model_dump(), user_id=user.id))
        if check_now:
            await watcher.check(w)
        return db.get_watch(w.id).to_dict()

    @app.put("/api/watches/{wid}")
    def update_watch(wid: int, body: WatchIn, user: User = Depends(current_user)):
        _validate(body)
        w = _get(wid, user)
        for k, v in body.model_dump().items():
            setattr(w, k, v)
        return db.update_watch(w).to_dict()

    @app.delete("/api/watches/{wid}")
    def delete_watch(wid: int, user: User = Depends(current_user)):
        _get(wid, user)
        db.delete_watch(wid)
        return {"ok": True}

    @app.post("/api/watches/{wid}/check")
    async def check_now(wid: int, user: User = Depends(current_user)):
        return await watcher.check(_get(wid, user))

    @app.get("/api/watches/{wid}/listings")
    def listings(wid: int, sort: str = "total", include_hidden: bool = False, site: str = "",
                 user: User = Depends(current_user)):
        w = _get(wid, user)
        items, sites = [], defaultdict(int)
        for r in db.get_listing_rows(wid):
            d = json.loads(r["data"])
            d["site"] = site_of(d["source"])
            d["first_seen"], d["first_total"] = r["first_seen"], r["first_price"]
            d["hidden"] = w.hide_risky and r["risk"] >= scoring.RISK_HIDE_THRESHOLD
            if d["hidden"] and not include_hidden:
                continue
            sites[d["site"]] += 1
            if site and d["site"] != site:
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
        return {"items": items, "sites": dict(sorted(sites.items()))}

    @app.get("/api/watches/{wid}/history")
    def history(wid: int, days: int = HISTORY_DAYS, user: User = Depends(current_user)):
        _get(wid, user)
        since = (utcnow() - timedelta(days=days)).isoformat()
        out = {}
        for b in ("new", "used"):
            med, n = scoring.median_of(db.reference_totals(wid, b, since, scoring.RISK_HIDE_THRESHOLD))
            out[b] = {"typical": med, "sample_size": n}
        return {"snapshots": db.snapshots(wid, since), "reference": out}

    @app.get("/api/alerts")
    def alerts(since_id: int = 0, user: User = Depends(current_user)):
        return db.list_alerts(user.id, since_id)

    @app.get("/api/status")
    def status(user: User = Depends(current_user)):
        channels = [n for n, on in (("ntfy", user.ntfy_topic), ("discord", user.discord_webhook)) if on]
        return {"sources": [s.name for s in enabled_sources()], "alert_channels": channels}

    return app


app = build_app()
