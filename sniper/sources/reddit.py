"""Reddit via the official API (free "script" app at reddit.com/prefs/apps).

- r/buildapcsales: retailer deals, price in the title -> condition new.
- r/hardwareswap: people selling used parts. "[USA-TX] [H] RTX 4060 [W] PayPal".
  Only the [H] (have) part is used, only posts where they want money, and the
  price comes from the post body. The seller's "Trades: N" flair counts as feedback.
"""
from __future__ import annotations

import base64
import os
import re
import time
from datetime import datetime, timezone

import httpx

from ..matching import normalize, tokens
from ..models import Listing, Watch
from .base import Source

TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
API = "https://oauth.reddit.com"
MAX_AGE_DAYS = 7

_PRICE = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{2}))?")
_HWS_TITLE = re.compile(r"^\s*\[(?P<loc>[A-Z]{2,3}(?:-[A-Z]{2})?)\]\s*\[H\](?P<have>.*?)\[W\](?P<want>.*)$", re.I)
_TRADES = re.compile(r"trades?\s*:\s*(\d+)", re.I)
_MONEY_WORDS = ("paypal", "cash", "venmo", "zelle", "money", "$")


def _price(m: re.Match) -> float:
    return float(m.group(1).replace(",", "") + "." + (m.group(2) or "00"))


def parse_bapcs(post: dict) -> Listing | None:
    """r/buildapcsales: "[GPU] MSI RTX 4060 Ventus ($299.99 - $20 MIR = $279.99)"."""
    title = post.get("title", "")
    flair = (post.get("link_flair_text") or "").lower()
    if "expired" in flair or "out of stock" in flair:
        return None
    prices = list(_PRICE.finditer(title))
    if not prices:
        return None
    eq = re.search(r"=\s*\$\s?(\d[\d,]*(?:\.\d{2})?)", title)        # "... = $279.99" is the final price
    price = float(eq.group(1).replace(",", "")) if eq else _price(prices[0])
    clean = re.sub(r"\(.*?\$.*?\)|\$\s?\d[\d,]*(?:\.\d{2})?", "", title)
    clean = re.sub(r"^\s*\[[^\]]+\]\s*", "", clean).strip(" -–|")
    free_ship = "free ship" in title.lower() or "shipped" in title.lower()
    return Listing(
        source="r/buildapcsales",
        source_id=post["id"],
        title=clean or title,
        url=post.get("url") if (post.get("url") or "").startswith("http") and "reddit.com" not in post.get("url", "")
        else "https://www.reddit.com" + post.get("permalink", ""),
        price=price,
        shipping=0.0 if free_ship else None,
        condition="refurbished" if "refurb" in title.lower() else "new",
        seller_name=post.get("domain"),            # the retailer
        description=f"reddit deal post by u/{post.get('author')}",
    )


def parse_hws(post: dict, query: str) -> Listing | None:
    """r/hardwareswap selling post. Price is looked up next to the item in the body."""
    m = _HWS_TITLE.match(post.get("title", ""))
    if not m:
        return None
    have, want = m.group("have").strip(), m.group("want").lower()
    if not any(w in want for w in _MONEY_WORDS):
        return None                                 # trade-only or a buying post
    if not all(t in normalize(have).split() for t in tokens(query)):
        return None                                 # the item is only in [W], not [H]
    body = post.get("selftext", "") or ""
    price = None
    q = tokens(query)
    for line in body.splitlines():                  # first price on a line that mentions the item
        if all(t in normalize(line).split() for t in q):
            pm = _PRICE.search(line)
            if pm:
                price = _price(pm)
                break
    if price is None:
        all_prices = list(_PRICE.finditer(body))
        if len(all_prices) == 1:                    # one price in the post: it's for this item
            price = _price(all_prices[0])
    if price is None:
        return None
    loc = m.group("loc").upper()
    trades = _TRADES.search(post.get("author_flair_text") or "")
    local = "local" in want or "cash" in want
    shipped = re.search(r"shipped|free shipping|shipping included", body, re.I) is not None
    return Listing(
        source="r/hardwareswap",
        source_id=post["id"],
        title=have,
        url="https://www.reddit.com" + post.get("permalink", ""),
        price=price,
        shipping=0.0 if shipped else None,
        condition="used",
        seller_name=post.get("author"),
        seller_feedback_count=int(trades.group(1)) if trades else 0,
        location=loc,
        local_pickup=False,
        taxable=False,
        description=(body[:300] + (" local pickup possible" if local else "")),
        listed_at=datetime.fromtimestamp(post.get("created_utc", 0), timezone.utc),
    )


class RedditSource(Source):
    key = "reddit"
    name = "Reddit"

    def __init__(self) -> None:
        self.client_id = os.getenv("REDDIT_CLIENT_ID", "")
        self.client_secret = os.getenv("REDDIT_CLIENT_SECRET", "")
        self.user_agent = os.getenv("REDDIT_USER_AGENT", "price-sniper/0.1 (personal deal watcher)")
        self._token: str | None = None
        self._expiry = 0.0

    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret)

    async def _auth(self, client: httpx.AsyncClient) -> dict:
        if not self._token or time.time() > self._expiry - 60:
            basic = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode()
            r = await client.post(TOKEN_URL, data={"grant_type": "client_credentials"},
                                  headers={"Authorization": f"Basic {basic}", "User-Agent": self.user_agent})
            r.raise_for_status()
            body = r.json()
            self._token, self._expiry = body["access_token"], time.time() + int(body.get("expires_in", 3600))
        return {"Authorization": f"Bearer {self._token}", "User-Agent": self.user_agent}

    async def _search(self, client, headers, sub: str, q: str) -> list[dict]:
        r = await client.get(f"{API}/r/{sub}/search", headers=headers,
                             params={"q": q, "restrict_sr": "1", "sort": "new", "t": "week", "limit": "50"})
        r.raise_for_status()
        return [c["data"] for c in r.json().get("data", {}).get("children", [])]

    async def search(self, watch: Watch, client: httpx.AsyncClient) -> list[Listing]:
        headers = await self._auth(client)
        cutoff = time.time() - MAX_AGE_DAYS * 86400
        out: list[Listing] = []
        for post in await self._search(client, headers, "buildapcsales", watch.query):
            if post.get("created_utc", 0) >= cutoff and (l := parse_bapcs(post)):
                out.append(l)
        for post in await self._search(client, headers, "hardwareswap", f'title:"{watch.query}"'):
            if (post.get("created_utc", 0) >= cutoff and "closed" not in (post.get("link_flair_text") or "").lower()
                    and (l := parse_hws(post, watch.query))):
                out.append(l)
        return out
