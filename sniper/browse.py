"""Browse: a feed of fresh deals from the deal subreddits, no watch needed.

One shared fetch for everyone, cached for a few minutes, so browsing costs a handful of
Reddit calls per 10 minutes no matter how many people use it.
"""
from __future__ import annotations

import asyncio
import os
import random
import re
import time
from datetime import datetime, timezone
from typing import Optional

import httpx

from .db import DB
from .matching import part_kind
from .sources.reddit import API, _HWS_TITLE, _MONEY_WORDS, _PRICE, _price, live_lines, parse_bapcs, reddit_image

CACHE_SECONDS = 600
MAX_AGE_DAYS = 7

# (subreddit, label shown in the app, kind of post)
FEEDS = [
    ("buildapcsales", "r/buildapcsales", "deal"),        # store deals: "[GPU] ... $279"
    ("hardwareswap", "r/hardwareswap", "swap"),          # used PC parts, people selling
    ("homelabsales", "r/homelabsales", "swap"),          # servers, networking, drives
    ("appleswap", "r/appleswap", "swap"),                # used Apple gear
    ("gamesale", "r/gamesale", "swap"),                  # games and consoles
]

CATEGORIES = ["GPU", "CPU", "RAM", "Storage", "Monitor", "Motherboard", "PSU", "Case & cooling",
              "Laptop", "Prebuilt", "Peripherals", "Audio", "Apple", "Games & consoles", "Networking & servers", "Other"]

# r/buildapcsales title tags -> category
_TAGS = {
    "gpu": "GPU", "cpu": "CPU", "ram": "RAM", "memory": "RAM", "ssd": "Storage", "hdd": "Storage", "nvme": "Storage",
    "m.2": "Storage", "storage": "Storage", "monitor": "Monitor", "motherboard": "Motherboard", "mobo": "Motherboard",
    "psu": "PSU", "case": "Case & cooling", "cooler": "Case & cooling", "fan": "Case & cooling", "fans": "Case & cooling",
    "aio": "Case & cooling", "laptop": "Laptop", "prebuilt": "Prebuilt", "keyboard": "Peripherals",
    "mouse": "Peripherals", "controller": "Peripherals", "webcam": "Peripherals", "headphones": "Audio",
    "headset": "Audio", "speakers": "Audio", "mic": "Audio", "router": "Networking & servers",
    "networking": "Networking & servers", "nas": "Networking & servers",
}
_KIND_CATEGORY = {"gpu": "GPU", "cpu": "CPU", "ram": "RAM", "storage": "Storage", "psu": "PSU", "board": "Motherboard"}
_WORD_CATEGORY = [
    (r"\b(monitor|display)\b", "Monitor"), (r"\b(laptop|notebook|macbook)\b", "Laptop"),
    (r"\b(prebuilt|gaming pc)\b", "Prebuilt"), (r"\b(keyboard|mouse|controller|webcam)\b", "Peripherals"),
    (r"\b(headphones?|headset|earbuds|speakers?|dac|amp)\b", "Audio"),
    (r"\b(iphone|ipad|airpods|imac|apple watch|mac mini|mac studio)\b", "Apple"),
    (r"\b(ps5|ps4|xbox|switch|nintendo|steam deck|playstation)\b", "Games & consoles"),
    (r"\b(case|cooler|aio|fans?)\b", "Case & cooling"),
    (r"\b(router|switch(?:es)? \d+ ?port|server|nas|rack|poe)\b", "Networking & servers"),
]


def guess_category(text: str, sub: str = "") -> str:
    low = text.lower()
    if m := re.match(r"\s*\[([^\]]+)\]", low):              # buildapcsales "[GPU] ..."
        tag = m.group(1).strip()
        if tag in _TAGS:
            return _TAGS[tag]
    if kind := part_kind(text):
        return _KIND_CATEGORY.get(kind, "Other")
    for rx, cat in _WORD_CATEGORY:
        if re.search(rx, low):
            return cat
    return {"homelabsales": "Networking & servers", "appleswap": "Apple", "gamesale": "Games & consoles"}.get(sub, "Other")


def parse_swap(post: dict, label: str, sub: str) -> Optional[dict]:
    """A "[USA-CA] [H] thing [W] PayPal" selling post. Price from the body when there's one."""
    title = post.get("title", "")
    flair = (post.get("link_flair_text") or "").lower()
    if any(w in flair for w in ("closed", "sold", "buying", "meta", "official")):
        return None
    m = _HWS_TITLE.match(title)
    if m:
        have, want = m.group("have").strip(), m.group("want").lower()
        if not any(w in want for w in _MONEY_WORDS):
            return None                     # trade-only or someone buying
    elif sub == "homelabsales" and re.match(r"\s*\[fs\]", title, re.I):
        have = re.sub(r"^\s*\[fs\]\s*(\[[^\]]*\]\s*)?", "", title, flags=re.I).strip()
    else:
        return None
    lines = live_lines(post.get("selftext", "") or "")
    prices = [_price(p) for p in _PRICE.finditer("\n".join(lines))]
    return {
        "id": f"{sub}:{post['id']}", "source": label, "title": have[:200],
        "price": min(prices) if prices else None, "price_note": "from" if len(prices) > 1 else "",
        "condition": "used", "category": guess_category(have, sub),
        "url": "https://www.reddit.com" + post.get("permalink", ""), "store": None,
        "location": m.group("loc").upper() if m else None, "created": post.get("created_utc", 0),
        "image": reddit_image(post),
    }


def parse_deal(post: dict, label: str) -> Optional[dict]:
    l = parse_bapcs(post)
    if not l:
        return None
    return {
        "id": f"buildapcsales:{post['id']}", "source": label, "title": l.title[:200], "price": l.price,
        "price_note": "", "condition": l.condition, "category": guess_category(post.get("title", ""), "buildapcsales"),
        "url": str(l.url), "store": l.seller_name, "location": None, "created": post.get("created_utc", 0),
        "image": l.image_url,
    }


def _demo_items() -> list[dict]:
    rnd = random.Random(42)
    now = time.time()
    rows = [("[GPU] MSI RTX 4060 Ventus 2X 8GB", "GPU", 279.99, "newegg.com"),
            ("[CPU] AMD Ryzen 7 7800X3D", "CPU", 339.00, "amazon.com"),
            ("[RAM] G.Skill Flare X5 32GB (2x16) DDR5 6000 CL30", "RAM", 89.99, "newegg.com"),
            ("[SSD] WD Black SN850X 2TB NVMe", "Storage", 119.99, "bestbuy.com"),
            ("[Monitor] LG 27GP850-B 27\" 1440p 180Hz", "Monitor", 249.99, "amazon.com"),
            ("[PSU] Corsair RM850e 850W 80+ Gold", "PSU", 94.99, "bestbuy.com"),
            ("[Case] Lian Li Lancool 216", "Case & cooling", 79.99, "newegg.com"),
            ("[Keyboard] Keychron V1 QMK", "Peripherals", 64.99, "amazon.com")]
    out = [{"id": f"demo:{i}", "source": "Demo deals", "title": re.sub(r"^\[[^\]]+\]\s*", "", t), "price": p,
            "price_note": "", "condition": "new", "category": c, "url": f"https://example.com/demo/browse-{i}",
            "store": s, "location": None, "image": None,
            "created": now - rnd.randint(300, 86400)} for i, (t, c, p, s) in enumerate(rows)]
    out += [{"id": "demo:hws1", "source": "Demo swaps", "title": "RTX 3080 FE 10GB", "price": 360.0, "price_note": "",
             "condition": "used", "category": "GPU", "url": "https://example.com/demo/browse-hws1", "store": None,
             "location": "USA-TX", "image": None, "created": now - 5400}]
    return out


class Browser:
    def __init__(self, db: DB, reddit_source=None, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.db = db
        self.reddit = reddit_source
        self._transport = transport
        self._items: list[dict] = []
        self._at = 0.0
        self._error: Optional[str] = None
        self._lock = asyncio.Lock()

    @property
    def live(self) -> bool:
        return bool(self.reddit and self.reddit.configured()) and os.getenv("SNIPER_BROWSE_DEMO") != "1"

    async def items(self) -> tuple[list[dict], float, Optional[str]]:
        if not self.live:
            return _demo_items(), time.time(), None
        async with self._lock:
            if time.time() - self._at > CACHE_SECONDS:
                await self._refresh()
        return self._items, self._at, self._error

    async def _refresh(self) -> None:
        limit = self.reddit.daily_limit()
        if limit and self.db.usage_today(self.reddit.key) + len(FEEDS) > limit * 0.9:
            self._error = "Reddit's daily limit is almost used up, so Browse shows older results until tomorrow."
            self._at = time.time()
            return
        cutoff = time.time() - MAX_AGE_DAYS * 86400
        out: list[dict] = []
        errors = []
        calls = 0
        async with httpx.AsyncClient(timeout=20, transport=self._transport) as client:
            try:
                headers = await self.reddit._auth(client)
                calls += 1
            except Exception as e:  # noqa: BLE001 - shown to the user as a note
                self._error = f"Couldn't sign in to Reddit ({type(e).__name__})."
                self._at = time.time()
                return
            for sub, label, kind in FEEDS:
                try:
                    r = await client.get(f"{API}/r/{sub}/new", headers=headers, params={"limit": "100"})
                    calls += 1
                    r.raise_for_status()
                    posts = [c["data"] for c in r.json().get("data", {}).get("children", [])]
                except Exception as e:  # noqa: BLE001 - one subreddit failing shouldn't hide the rest
                    errors.append(f"{label}: {type(e).__name__}")
                    continue
                for p in posts:
                    if p.get("created_utc", 0) < cutoff or p.get("stickied") or p.get("removed_by_category"):
                        continue
                    item = parse_deal(p, label) if kind == "deal" else parse_swap(p, label, sub)
                    if item and str(item["url"]).startswith(("https://", "http://")):
                        out.append(item)
        self.db.add_usage(self.reddit.key, calls)
        out.sort(key=lambda i: i["created"], reverse=True)
        if out or not self._items:
            self._items = out
        self._error = ("Some subreddits didn't load: " + ", ".join(errors)) if errors else None
        self._at = time.time()


def filter_items(items: list[dict], category: str = "", source: str = "", q: str = "",
                 max_price: Optional[float] = None, sort: str = "new") -> list[dict]:
    from .matching import missing_query_word
    out = [i for i in items
           if (not category or i["category"] == category)
           and (not source or i["source"] == source)
           and (not q or missing_query_word(i["title"], q) is None)
           and (max_price is None or (i["price"] is not None and i["price"] <= max_price))]
    if sort == "price":
        out.sort(key=lambda i: (i["price"] is None, i["price"] or 0))
    return out


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()
