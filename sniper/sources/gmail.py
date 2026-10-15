"""Listings from marketplace alert emails in the user's own Gmail (read-only OAuth).

Users turn on saved-search email alerts on Craigslist, OfferUp, Mercari, Facebook,
etc. This source reads those emails and pulls out listing links, titles and prices.
No marketplace passwords and no scraping: it only reads mail the user already got.
"""
from __future__ import annotations

import base64
import html
import os
import re
from urllib.parse import unquote, urlencode

import httpx

from ..matching import tokens
from ..models import Listing, Watch
from .base import Source

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"
SCOPE = "https://www.googleapis.com/auth/gmail.readonly"

SENDERS = ["craigslist.org", "offerup.com", "mercari.com", "facebookmail.com", "swappa.com", "nextdoor.com"]

# (source name, regex for a listing URL, group that is a stable id)
LISTING_PATTERNS = [
    ("Craigslist", re.compile(r"https?://[a-z0-9.-]*craigslist\.org/[^\s\"'<>]*?/(\d{8,})\.html"), 1),
    ("OfferUp", re.compile(r"https?://(?:www\.)?offerup\.com/item/detail/([\w-]+)"), 1),
    ("Mercari", re.compile(r"https?://(?:www\.)?mercari\.com/us/item/(m\d+)"), 1),
    ("FB Marketplace", re.compile(r"https?://(?:www\.|m\.)?facebook\.com/marketplace/item/(\d+)"), 1),
    ("Swappa", re.compile(r"https?://(?:www\.)?swappa\.com/listing/(?:view/)?([A-Z0-9]+)"), 1),
]
_A_RE = re.compile(r"<a\b[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", re.I | re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_PRICE_RE = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})*(?:\.\d{2})?|\d+(?:\.\d{2})?)")


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(_TAG_RE.sub(" ", fragment))).strip()


def _match_listing(href: str) -> tuple[str, str, str] | None:
    """(source name, id, clean url) if href points at a listing, even through a tracking redirect."""
    for candidate in (href, unquote(href), unquote(unquote(href))):
        for name, pat, grp in LISTING_PATTERNS:
            m = pat.search(candidate)
            if m:
                return name, m.group(grp), m.group(0)
    return None


def parse_alert_html(body: str) -> list[Listing]:
    """Extract listings from one alert email. Best-effort: email layouts vary.

    Alert emails usually link each listing several times (photo, title, price),
    so links to the same listing are merged: the longest real title wins and the
    first price found is kept.
    """
    found: dict[str, dict] = {}
    for a in _A_RE.finditer(body):
        hit = _match_listing(html.unescape(a.group(1)))
        if not hit:
            continue
        name, sid, url = hit
        anchor = _text(a.group(2))
        after = _text(body[a.end(): a.end() + 600])
        entry = found.setdefault(f"{name}:{sid}", {"name": name, "sid": sid, "url": url, "title": "", "fallback": "", "price": None})
        is_title = anchor and not _PRICE_RE.fullmatch(anchor)
        if is_title and len(anchor) > len(entry["title"]):
            entry["title"] = anchor
        if entry["price"] is None:
            pm = _PRICE_RE.search(anchor) or _PRICE_RE.search(after)
            if pm:
                entry["price"] = float(pm.group(1).replace(",", ""))
        if not entry["fallback"] and after:
            # text after a photo/price link, used only if no link has real title text
            entry["fallback"] = _PRICE_RE.sub("", after)[:120].strip()

    out = []
    for e in found.values():
        e["title"] = e["title"] or e["fallback"]
        if not e["price"] or not e["title"]:
            continue
        shipped = e["name"] == "Mercari"
        out.append(Listing(
            source=e["name"], source_id=e["sid"], title=e["title"][:200], url=e["url"], price=e["price"],
            condition="used", local_pickup=not shipped, taxable=False, shipping=None if shipped else 0.0,
        ))
    return out


def _bodies(payload: dict) -> list[str]:
    out = []
    if payload.get("mimeType") == "text/html" and payload.get("body", {}).get("data"):
        out.append(base64.urlsafe_b64decode(payload["body"]["data"] + "==").decode("utf-8", "replace"))
    for p in payload.get("parts", []) or []:
        out.extend(_bodies(p))
    return out


# --- OAuth helpers used by the web app -----------------------------------
def client_id() -> str:
    return os.getenv("GOOGLE_CLIENT_ID", "")


def client_secret() -> str:
    return os.getenv("GOOGLE_CLIENT_SECRET", "")


def authorize_url(state: str, redirect_uri: str) -> str:
    return AUTH_URL + "?" + urlencode({
        "client_id": client_id(), "redirect_uri": redirect_uri, "response_type": "code",
        "scope": SCOPE, "access_type": "offline", "prompt": "consent", "state": state,
    })


async def exchange_code(client: httpx.AsyncClient, code: str, redirect_uri: str) -> tuple[str, str]:
    """Return (refresh_token, gmail_address)."""
    r = await client.post(TOKEN_URL, data={
        "code": code, "client_id": client_id(), "client_secret": client_secret(),
        "redirect_uri": redirect_uri, "grant_type": "authorization_code",
    })
    r.raise_for_status()
    tok = r.json()
    prof = await client.get(f"{GMAIL}/profile", headers={"Authorization": f"Bearer {tok['access_token']}"})
    prof.raise_for_status()
    return tok["refresh_token"], prof.json()["emailAddress"]


async def access_token(client: httpx.AsyncClient, refresh_token: str) -> str:
    r = await client.post(TOKEN_URL, data={
        "refresh_token": refresh_token, "client_id": client_id(), "client_secret": client_secret(),
        "grant_type": "refresh_token",
    })
    r.raise_for_status()
    return r.json()["access_token"]


class GmailAlertsSource(Source):
    key = "gmail"
    name = "Email alerts"
    db = None   # set by the app; needed to look up each user's Gmail token

    def configured(self) -> bool:
        return bool(client_id() and client_secret())

    async def search(self, watch: Watch, client: httpx.AsyncClient) -> list[Listing]:
        if self.db is None or watch.user_id is None:
            return []
        refresh = self.db.get_gmail_token(watch.user_id)
        if not refresh:
            return []    # this user hasn't connected Gmail; not an error
        try:
            auth = {"Authorization": f"Bearer {await access_token(client, refresh)}"}
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (400, 401) and "invalid_grant" in e.response.text:
                # Google expired or revoked the connection (in "Testing" mode this happens every 7 days).
                self.db.expire_gmail(watch.user_id)
                raise RuntimeError("Gmail connection expired. Reconnect it in Settings") from None
            raise
        words = " ".join(tokens(watch.query))
        q = f"newer_than:14d ({' OR '.join('from:' + s for s in SENDERS)}) {words}"
        r = await client.get(f"{GMAIL}/messages", headers=auth, params={"q": q, "maxResults": "25"})
        r.raise_for_status()
        out: dict[str, Listing] = {}
        for msg in r.json().get("messages", []):
            m = await client.get(f"{GMAIL}/messages/{msg['id']}", headers=auth, params={"format": "full"})
            if m.status_code != 200:
                continue
            for body in _bodies(m.json().get("payload", {})):
                for l in parse_alert_html(body):
                    out.setdefault(l.key, l)
        return list(out.values())
