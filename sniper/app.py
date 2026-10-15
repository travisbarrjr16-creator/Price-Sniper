"""HTTP API + web UI. Run with:  uvicorn sniper.app:app --port 8000"""
from __future__ import annotations

import json
import logging
import re
import statistics
import os
import secrets
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from typing import Annotated, Optional

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi import Path as PathParam
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from . import budget, maintenance, scoring
from .alerts import valid_discord_webhook, valid_ntfy_topic
from .db import DB
from .models import CONDITIONS, User, Watch, utcnow
from .sources import ALL_SOURCES, enabled_sources, get_source, seller_key, site_of
from .sources import gmail
from .watcher import HISTORY_DAYS, Watcher

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
STATIC = Path(__file__).parent / "static"
log = logging.getLogger("sniper.app")
COOKIE = "sniper_session"


WatchId = Annotated[int, PathParam(ge=1, le=2**62)]   # huge ids would overflow SQLite


class WatchIn(BaseModel):
    query: str = Field(min_length=2, max_length=120)
    max_price: Optional[float] = Field(None, gt=0, le=1_000_000)
    target_price: Optional[float] = Field(None, gt=0, le=1_000_000)
    conditions: list[str] = Field(["new", "open_box", "refurbished", "used"], min_length=1)
    zip_code: Optional[str] = Field(None, max_length=10)
    radius_miles: Optional[int] = Field(None, ge=1, le=500)
    required_terms: list[str] = Field([], max_length=20)
    excluded_terms: list[str] = Field([], max_length=50)
    sources: list[str] = []
    tax_rate: float = Field(0.0, ge=0, le=0.25)
    interval_minutes: int = Field(15, ge=5, le=10080)

    @field_validator("query")
    @classmethod
    def _clean_query(cls, v: str) -> str:
        v = " ".join(v.split())          # collapse tabs/newlines/extra spaces
        if len(v) < 2:
            raise ValueError("enter at least 2 characters")
        return v

    @field_validator("zip_code")
    @classmethod
    def _zip(cls, v: Optional[str]) -> Optional[str]:
        v = (v or "").strip()
        if not v:
            return None
        if not re.fullmatch(r"\d{5}", v):
            raise ValueError("ZIP code must be 5 digits")
        return v

    @field_validator("required_terms", "excluded_terms")
    @classmethod
    def _terms(cls, v: list[str]) -> list[str]:
        out = [" ".join(t.split()) for t in v]
        if any(len(t) > 60 for t in out):
            raise ValueError("each word or phrase must be 60 characters or less")
        return [t for t in out if t]     # drop blanks
    strict_variants: bool = True
    hide_risky: bool = True
    include_auctions: bool = False
    active: bool = True


class Credentials(BaseModel):
    email: str = Field(min_length=3, max_length=200)
    password: str = Field(min_length=8, max_length=200)
    invite_code: Optional[str] = None


class NoticeIn(BaseModel):
    seconds: int = Field(60, ge=10, le=3600)
    message: Optional[str] = Field(None, max_length=300)


class QuietIn(BaseModel):
    start: Optional[str] = None      # "23:00", or null to turn quiet hours off
    end: Optional[str] = None
    tz: Optional[str] = Field(None, max_length=64)
    allow_urgent: bool = True


class HideIn(BaseModel):
    kind: str                        # "seller" or "listing"
    value: str = Field(min_length=1, max_length=300)
    label: Optional[str] = Field(None, max_length=200)


class DeleteMeIn(BaseModel):
    password: str = Field(min_length=1, max_length=200)


class ResetIn(BaseModel):
    token: str = Field(min_length=10, max_length=200)
    password: str = Field(min_length=8, max_length=200)


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
    boot_id = secrets.token_hex(8)   # changes on every restart, so browsers know a new version is up
    oauth_states: dict[str, tuple[int, float]] = {}
    login_failures: dict[str, list[float]] = defaultdict(list)

    @asynccontextmanager
    async def lifespan(_app):
        maintenance.clear_on_startup(db)
        if background:
            watcher.start()
        yield
        await watcher.stop()

    app = FastAPI(title="Price Sniper", lifespan=lifespan)

    @app.middleware("http")
    async def protect(request: Request, call_next):
        # Requests that change things must come from this app's own pages, not another website
        # (defense in depth on top of SameSite cookies and the JSON-only API).
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            origin = request.headers.get("origin")
            if origin and origin != "null":
                origin_host = origin.split("://", 1)[-1].rstrip("/").lower()
                ours = {request.headers.get("host", ""), request.headers.get("x-forwarded-host", ""),
                        public_url().split("://", 1)[-1]}       # proxies like Tailscale may rewrite Host
                if origin_host not in {h.lower() for h in ours if h}:
                    log.warning("blocked request from %s (PUBLIC_URL is %s)", origin, public_url())
                    return JSONResponse({"detail": f"Blocked: this page's address ({origin}) doesn't match the "
                                                   f"app's PUBLIC_URL ({public_url()}). If this is your app, set "
                                                   f"PUBLIC_URL={origin} in .env and restart."}, status_code=403)
        response = await call_next(request)
        h = response.headers
        h.setdefault("X-Content-Type-Options", "nosniff")
        h.setdefault("X-Frame-Options", "DENY")                 # can't be framed by other sites
        h.setdefault("Referrer-Policy", "no-referrer")          # stores don't see your app's address when you tap Open
        h.setdefault("Content-Security-Policy",
                     "default-src 'self'; img-src 'self' https: data:; style-src 'self' 'unsafe-inline'; "
                     "script-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; "
                     "form-action 'self'")
        return response
    app.state.db, app.state.watcher = db, watcher

    # --- maintenance notice (public: shows on the sign-in screen too) --------
    @app.get("/api/notice")
    def notice():
        return {"maintenance": maintenance.current(db), "boot_id": boot_id}

    def require_admin(request: Request) -> None:
        token = os.getenv("SNIPER_ADMIN_TOKEN")
        given = request.headers.get("authorization", "").removeprefix("Bearer ")
        if not token or not secrets.compare_digest(given, token):
            raise HTTPException(403, "admin token required")

    @app.post("/api/admin/maintenance", dependencies=[Depends(require_admin)])
    def announce(body: NoticeIn):
        return maintenance.announce(db, body.seconds, body.message)

    @app.delete("/api/admin/maintenance", dependencies=[Depends(require_admin)])
    def cancel_notice():
        maintenance.cancel(db)
        return {"ok": True}

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
                "gmail_connected": db.gmail_connected(user.id),
                "push_error": db.push_error(user.id),
                "invite_required": bool(os.getenv("SNIPER_INVITE_CODE"))}

    @app.put("/api/me")
    def update_me(body: SettingsIn, user: User = Depends(current_user)):
        topic = (body.ntfy_topic or "").strip() or None
        hook = (body.discord_webhook or "").strip() or None
        # Anyone who guesses an ntfy.sh topic can read it, so new topics must be long enough to be unguessable.
        if topic and topic != user.ntfy_topic and not (valid_ntfy_topic(topic) and len(topic) >= 12):
            raise HTTPException(422, "ntfy topic: at least 12 letters, numbers, - or _ (use Generate for a safe one)")
        if not valid_ntfy_topic(topic):
            raise HTTPException(422, "ntfy topic: 6-64 letters, numbers, - or _")
        if not valid_discord_webhook(hook):
            raise HTTPException(422, "Discord webhook must be a https://discord.com/api/webhooks/... URL")
        return db.update_user_settings(user.id, topic, hook).public()

    @app.post("/api/me/delete")
    def delete_me(body: DeleteMeIn, request: Request, response: Response, user: User = Depends(current_user)):
        if not db.authenticate(user.email, body.password):
            raise HTTPException(401, "wrong password")
        db.delete_user(user.id)
        response.delete_cookie(COOKIE)
        return {"ok": True}

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/sw.js")
    def service_worker():          # served from the root so it can cover the whole app
        return FileResponse(STATIC / "sw.js", media_type="text/javascript", headers={"Cache-Control": "no-cache"})

    @app.get("/manifest.webmanifest")
    def manifest():
        return FileResponse(STATIC / "manifest.webmanifest", media_type="application/manifest+json")

    @app.get("/privacy")
    def privacy():
        return FileResponse(STATIC / "privacy.html")

    last_test: dict[int, float] = {}

    @app.post("/api/me/test-alert")
    async def test_alert(user: User = Depends(current_user)):
        """Send a sample notification to the user's own channels, so they can check the setup works."""
        if not (user.ntfy_topic or user.discord_webhook):
            raise HTTPException(400, "Add an ntfy topic or Discord webhook first, then Save")
        if time.time() - last_test.get(user.id, 0) < 20:
            raise HTTPException(429, "Wait a few seconds before sending another test")
        last_test[user.id] = time.time()
        async with httpx.AsyncClient(timeout=15) as client:
            results = await watcher.notifier.send(
                client, "🎯 Price Sniper test", "If you can read this, deal alerts will reach you here. "
                "Tap to open the app.", public_url(), ntfy_topic=user.ntfy_topic, discord_webhook=user.discord_webhook)
        results = results or {}
        db.record_push_result(user.id, results)
        failed = {k: v for k, v in results.items() if v}
        if failed:
            raise HTTPException(502, "Not delivered: " + "; ".join(f"{k} {v}" for k, v in failed.items()))
        return {"ok": True, "sent_to": [k for k in results] or ["(none)"]}

    @app.put("/api/me/quiet")
    def update_quiet(body: QuietIn, user: User = Depends(current_user)):
        hhmm = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
        start, end = (body.start or None), (body.end or None)
        if bool(start) != bool(end) or (start and not (hhmm.match(start) and hhmm.match(end))):
            raise HTTPException(422, "quiet hours need a start and end like 23:00 and 07:00")
        if body.tz:
            from zoneinfo import ZoneInfo
            try:
                ZoneInfo(body.tz)
            except Exception:
                raise HTTPException(422, "unknown time zone")
        return db.update_quiet_hours(user.id, start, end, body.tz, body.allow_urgent).public()

    @app.post("/api/auth/reset")
    def reset_password(body: ResetIn, response: Response):
        user = db.use_reset_token(body.token, body.password)
        if not user:
            raise HTTPException(400, "this reset link is invalid or expired; ask for a new one")
        _start_session(response, user)
        return user.public()

    # --- muted sellers & "not interested" --------------------------------------
    @app.get("/api/hidden")
    def list_hidden(user: User = Depends(current_user)):
        return db.list_hidden(user.id)

    @app.post("/api/hidden")
    def hide(body: HideIn, user: User = Depends(current_user)):
        if body.kind not in ("seller", "listing"):
            raise HTTPException(422, "kind must be seller or listing")
        db.hide(user.id, body.kind, body.value, body.label)
        return {"ok": True}

    @app.delete("/api/hidden")
    def unhide(kind: str, value: str, user: User = Depends(current_user)):
        db.unhide(user.id, kind, value)
        return {"ok": True}

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
                        "latest_found": max((r["first_seen"] for r in rows), default=None),
                        "stats": watcher.last_stats.get(w.id)})
        return out

    @app.post("/api/watches")
    async def create_watch(body: WatchIn, check_now: bool = True, user: User = Depends(current_user)):
        _validate(body)
        if len(db.list_watches(user.id)) >= int(os.getenv("SNIPER_MAX_WATCHES", "25")):
            raise HTTPException(429, "watch limit reached; delete one first")
        if why := budget.check_watch(db, Watch(**body.model_dump(), user_id=user.id), enabled_sources()):
            raise HTTPException(429, why)
        w = db.create_watch(Watch(**body.model_dump(), user_id=user.id))
        if check_now:
            await watcher.check(w)
        return db.get_watch(w.id).to_dict()

    @app.put("/api/watches/{wid}")
    def update_watch(wid: WatchId, body: WatchIn, user: User = Depends(current_user)):
        _validate(body)
        w = _get(wid, user)
        for k, v in body.model_dump().items():
            setattr(w, k, v)
        if why := budget.check_watch(db, w, enabled_sources()):
            raise HTTPException(429, why)
        return db.update_watch(w).to_dict()

    @app.delete("/api/watches/{wid}")
    def delete_watch(wid: WatchId, user: User = Depends(current_user)):
        _get(wid, user)
        db.delete_watch(wid)
        return {"ok": True}

    @app.post("/api/watches/{wid}/check")
    async def check_now(wid: WatchId, user: User = Depends(current_user)):
        return await watcher.check(_get(wid, user))

    @app.get("/api/watches/{wid}/listings")
    def listings(wid: WatchId, sort: str = "total", include_hidden: bool = False, include_dismissed: bool = False,
                 site: str = "", user: User = Depends(current_user)):
        w = _get(wid, user)
        muted, dismissed = db.hidden_sets(user.id)
        shown, sites = [], defaultdict(int)
        hidden_by_you = 0
        for r in db.get_listing_rows(wid):
            d = json.loads(r["data"])
            d["site"] = site_of(d["source"])
            d["seller_key"] = seller_key(d["source"], d.get("seller_name"))
            d["first_seen"], d["first_total"] = r["first_seen"], r["first_price"]
            d["hidden"] = w.hide_risky and r["risk"] >= scoring.RISK_HIDE_THRESHOLD
            d["dismissed"] = d["key"] in dismissed
            d["muted"] = d["seller_key"] in muted
            d["target_hit"] = bool(w.target_price and d["total_cost"] <= w.target_price)
            if d["hidden"] and not include_hidden:
                continue
            if d["dismissed"] or d["muted"]:
                hidden_by_you += 1
                if not include_dismissed:
                    continue
            shown.append(d)
        # "N cheaper right now" counts every real option, on every site
        live = sorted(d["total_cost"] for d in shown if not (d["hidden"] or d["dismissed"] or d["muted"]))
        items = []
        for d in shown:
            d["cheaper_now"] = sum(1 for t in live if t < d["total_cost"])
            sites[d["site"]] += 1
            if not site or d["site"] == site:
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
        return {"items": items, "sites": dict(sorted(sites.items())), "hidden_by_you": hidden_by_you}

    @app.get("/api/watches/{wid}/history")
    def history(wid: WatchId, days: int = HISTORY_DAYS, user: User = Depends(current_user)):
        _get(wid, user)
        since = (utcnow() - timedelta(days=days)).isoformat()
        out = {}
        for b in ("new", "used"):
            totals = db.reference_totals(wid, b, since, scoring.RISK_HIDE_THRESHOLD)
            med, n = scoring.median_of(totals)
            low = high = None
            if n >= 4:      # middle half of prices = "typical range"
                q = statistics.quantiles(totals, n=4)
                low, high = round(q[0], 2), round(q[2], 2)
            out[b] = {"typical": med, "sample_size": n, "low": low, "high": high}
        return {"snapshots": db.snapshots(wid, since), "reference": out,
                "lowest_seen": db.lowest_seen(wid, scoring.RISK_HIDE_THRESHOLD)}

    @app.get("/api/usage")
    def usage(user: User = Depends(current_user)):
        """Shared daily limits per site: used today, and what everyone's watches need per day."""
        return budget.report(db, enabled_sources())

    @app.get("/api/alerts")
    def alerts(since_id: int = 0, user: User = Depends(current_user)):
        return db.list_alerts(user.id, since_id)

    @app.get("/api/status")
    def status(user: User = Depends(current_user)):
        channels = [n for n, on in (("ntfy", user.ntfy_topic), ("discord", user.discord_webhook)) if on]
        return {"sources": [s.name for s in enabled_sources()], "alert_channels": channels}

    return app


app = build_app()
