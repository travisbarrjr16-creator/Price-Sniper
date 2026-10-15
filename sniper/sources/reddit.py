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

from ..matching import missing_query_word
from ..models import Listing, Watch
from .base import Source

TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
API = "https://oauth.reddit.com"
MAX_AGE_DAYS = 7

_PRICE = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{1,2}))?(\s?k\b)?", re.I)   # $230, $1,299.99, $1.2k
_STRIKE = re.compile(r"~~.*?~~", re.S)
# "SOLD", "sold for $430", "traded to u/x", "pending", "gone" mark an item as gone. Only a few phrasings
# are ordinary wording: "sold as-is", "not sold separately", "can be sold together", "price has gone down".
_SOLD = re.compile(r"\bpending\b"
                   r"|(?<!not )(?<!be )(?<!been )\b(sold|traded)\b(?! (as[- ]?is|separately|together|individually|as a)\b)"
                   r"|(?<!has )(?<!have )(?<!had )(?<!it )(?<!them )(?<!these )(?<!this )(?<!everything )\bgone\b"
                   r"(?! (down|up|asap|soon|quick|quickly|fast|by)\b)", re.I)
_HWS_TITLE = re.compile(r"^\s*\[(?P<loc>[A-Z]{2,3}(?:-[A-Z]{2})?)\]\s*\[H\](?P<have>.*?)\[W\](?P<want>.*)$", re.I)
_TRADES = re.compile(r"trades?\s*:\s*(\d+)", re.I)
_MONEY_WORDS = ("paypal", "cash", "venmo", "zelle", "money", "$")


def _price(m: re.Match) -> float:
    value = float(m.group(1).replace(",", "") + "." + (m.group(2) or "00"))
    return value * 1000 if m.group(3) else value


def live_lines(body: str) -> list[str]:
    """Body lines with what's no longer for sale removed.

    Sellers edit posts instead of deleting them: crossed-out text (~~old~~) is an old price or a
    sold item, and lines saying SOLD / traded / pending are gone. Only what's left is current.
    """
    out = []
    for line in body.splitlines():
        if _SOLD.search(line):
            continue
        line = _STRIKE.sub(" ", line)
        if line.strip():
            out.append(line)
    return out


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
    if missing_query_word(have, query) is not None:
        return None                                 # the item is only in [W], not [H]
    body = post.get("selftext", "") or ""
    lines = live_lines(body)
    price = None
    for line in lines:                              # first current price on a line that mentions the item
        if missing_query_word(line, query) is None:
            pm = _PRICE.search(line)
            if pm:
                price = _price(pm)
                break
    if price is None:
        all_prices = list(_PRICE.finditer("\n".join(lines)))
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
    default_daily_limit = 50000     # Reddit limits per minute; this keeps the daily total modest

    def __init__(self) -> None:
        self.client_id = os.getenv("REDDIT_CLIENT_ID", "")
        self.client_secret = os.getenv("REDDIT_CLIENT_SECRET", "")
        # Reddit's required format: <platform>:<app id>:<version> (by /u/<username>). Generic ones get throttled.
        who = os.getenv("REDDIT_USERNAME", "").strip().lstrip("/").removeprefix("u/")
        self.user_agent = os.getenv("REDDIT_USER_AGENT") or (
            f"python:price-sniper:v0.2 (by /u/{who})" if who else "python:price-sniper:v0.2 (personal deal watcher)")
        self._token: str | None = None
        self._expiry = 0.0

    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret)

    def calls_per_check(self, watch: Watch) -> int:
        return 2        # r/buildapcsales + r/hardwareswap

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
        params = {"q": q, "restrict_sr": "1", "sort": "new", "t": "week", "limit": "50"}
        r = await client.get(f"{API}/r/{sub}/search", headers=headers, params=params)
        if r.status_code == 401:            # token expired/revoked early: fresh token, retry once
            self._token = None
            headers.update(await self._auth(client))
            r = await client.get(f"{API}/r/{sub}/search", headers=headers, params=params)
        if r.status_code == 429:
            wait = r.headers.get("x-ratelimit-reset") or r.headers.get("retry-after") or "?"
            raise RuntimeError(f"Reddit says slow down (rate limited); try again in about {wait}s")
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
