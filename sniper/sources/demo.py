"""A simulated marketplace so the app works with zero API keys.

It keeps a small evolving "market" per query: listings sell (disappear), new
ones appear, prices get cut. It deliberately includes the junk real searches
return (Ti variants, empty boxes, scams) so the filters have something to do.
Enable with SNIPER_DEMO=1 (on by default when no real source is configured).
"""
from __future__ import annotations

import hashlib
import os
import random
import re

import httpx

from ..models import Listing, Watch
from .base import Source

GPU_BRANDS = ["ASUS Dual", "MSI Ventus 2X", "Gigabyte Eagle OC", "Zotac Twin Edge", "PNY Verto", "MSI Gaming X", "ASUS TUF"]
GENERIC = ["", "Brand New", "Lightly Used", "Mint", "Great Condition", "Like New"]
CITIES = ["Downtown", "Northside", "Eastgate", "Westfield", "Riverside", "Oak Park", "Lakeview", "Hillcrest"]


def _base_price(watch: Watch) -> float:
    if watch.max_price:
        return watch.max_price * 1.12
    h = int(hashlib.sha1(watch.query.lower().encode()).hexdigest()[:6], 16)
    return 120 + h % 500


class DemoSource(Source):
    key = "demo"
    name = "Demo"

    def __init__(self) -> None:
        self._markets: dict[str, dict] = {}

    def configured(self) -> bool:
        return os.getenv("SNIPER_DEMO", "") == "1"

    def _new_listing(self, rng: random.Random, watch: Watch, base: float, n: int) -> Listing:
        q = watch.query.strip()
        is_gpu = bool(re.search(r"\b(rtx|gtx|rx|arc)\b", q.lower()))
        brand = rng.choice(GPU_BRANDS if is_gpu else GENERIC)
        extra = " 8GB GDDR6" if is_gpu else ""
        kind = rng.choices(
            ["retail", "used_ship", "local", "refurb", "open_box", "variant", "junk", "scam"],
            weights=[18, 26, 24, 8, 8, 8, 4, 4],
        )[0]
        sid = f"{kind}-{n}"
        title = f"{brand} {q}{extra}".strip()
        common = dict(source_id=sid, url=f"https://example.com/demo/{sid}")

        if kind == "retail":
            return Listing(source="Demo Retail", title=title, price=round(base * rng.uniform(0.98, 1.12), 2),
                           shipping=0.0, condition="new", seller_name="BigBoxStore", seller_rating=99.1,
                           seller_feedback_count=250000, **common)
        if kind == "open_box":
            return Listing(source="Demo Retail", title=f"{title} (Open Box)", price=round(base * rng.uniform(0.85, 0.95), 2),
                           shipping=0.0, condition="open_box", seller_name="BigBoxStore", seller_rating=99.1,
                           seller_feedback_count=250000, **common)
        if kind == "refurb":
            return Listing(source="Demo Auctions", title=f"{title} Refurbished", price=round(base * rng.uniform(0.78, 0.9), 2),
                           shipping=rng.choice([0.0, 9.99]), condition="refurbished", seller_name="refurb_depot",
                           seller_rating=98.7, seller_feedback_count=8000, **common)
        if kind == "used_ship":
            return Listing(source="Demo Auctions", title=f"{title} - used, works great", price=round(base * rng.uniform(0.7, 0.92), 2),
                           shipping=rng.choice([None, 0.0, 12.5, 18.0]), condition="used",
                           seller_name=f"user{rng.randint(100, 999)}", seller_rating=round(rng.uniform(96, 100), 1),
                           seller_feedback_count=rng.randint(15, 3000), **common)
        if kind == "local":
            return Listing(source="Demo Local", title=f"{title}", price=round(base * rng.uniform(0.62, 0.88) / 5) * 5,
                           condition="used", local_pickup=True, taxable=False,
                           distance_miles=round(rng.uniform(1, 60), 1), location=rng.choice(CITIES),
                           seller_name=f"local_{rng.randint(10, 99)}", seller_feedback_count=rng.randint(0, 80), **common)
        if kind == "variant":
            v = "Ti" if is_gpu else "Pro"
            return Listing(source="Demo Auctions", title=f"{brand} {q} {v}{extra}".strip(), price=round(base * rng.uniform(1.1, 1.4), 2),
                           shipping=0.0, condition=rng.choice(["new", "used"]), seller_name="gpu_flipper",
                           seller_rating=99.5, seller_feedback_count=900, **common)
        if kind == "junk":
            return Listing(source="Demo Local", title=f"{q} empty box only", price=round(rng.uniform(10, 30)),
                           condition="used", local_pickup=True, taxable=False, distance_miles=round(rng.uniform(1, 30), 1),
                           seller_feedback_count=3, **common)
        # scam
        return Listing(source="Demo Local", title=f"{title} NEW SEALED", price=round(base * rng.uniform(0.35, 0.5)),
                       condition="new", local_pickup=True, taxable=False, distance_miles=round(rng.uniform(5, 40), 1),
                       seller_feedback_count=0, description="shipping only, zelle only, text me",
                       location=rng.choice(CITIES), **common)

    async def search(self, watch: Watch, client: httpx.AsyncClient) -> list[Listing]:
        key = watch.query.lower().strip()
        base = _base_price(watch)
        m = self._markets.get(key)
        if m is None:
            rng = random.Random(key)
            m = {"rng": rng, "n": 0, "items": {}}
            for _ in range(22):
                m["n"] += 1
                l = self._new_listing(rng, watch, base, m["n"])
                m["items"][l.source_id] = l
            self._markets[key] = m
        else:
            rng = m["rng"]
            items = m["items"]
            for sid in list(items):                 # some sell
                if rng.random() < 0.12:
                    del items[sid]
            for l in items.values():                # some get price cuts
                if rng.random() < 0.15:
                    l.price = round(l.price * rng.uniform(0.9, 0.97), 2)
            for _ in range(rng.randint(1, 4)):      # new ones appear
                m["n"] += 1
                l = self._new_listing(rng, watch, base, m["n"])
                items[l.source_id] = l
        return [Listing(**{**l.__dict__}) for l in m["items"].values()]
