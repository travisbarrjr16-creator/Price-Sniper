"""Reverb (music gear + some electronics) via its public API. Free personal token
from reverb.com → Settings → API & Integrations."""
from __future__ import annotations

import os

import httpx

from ..models import Listing, Watch
from .base import Source

API = "https://api.reverb.com/api/listings"

CONDITION_MAP = {
    "brand new": "new", "b-stock": "open_box", "mint": "used", "excellent": "used",
    "very good": "used", "good": "used", "fair": "used", "poor": "used", "non functioning": "parts",
}


def _amount(v) -> float | None:
    try:
        return float((v or {}).get("amount"))
    except (TypeError, ValueError):
        return None


def parse_listings(data: dict) -> list[Listing]:
    out = []
    for it in data.get("listings", []):
        price = _amount(it.get("price"))
        if price is None or (it.get("price") or {}).get("currency", "USD") != "USD":   # never count €/£ as $
            continue
        cond = CONDITION_MAP.get(str((it.get("condition") or {}).get("display_name", "")).lower(), "used")
        ship = it.get("shipping") or {}
        rates = [_amount(r.get("rate")) for r in ship.get("rates", []) if r.get("region_code") in ("US", "US_CON")]
        rates = [r for r in rates if r is not None]
        photos = it.get("photos") or []
        out.append(Listing(
            source="Reverb",
            source_id=str(it["id"]),
            title=it.get("title", ""),
            url=(((it.get("_links") or {}).get("web") or {}).get("href")) or f"https://reverb.com/item/{it['id']}",
            price=price,
            shipping=min(rates) if rates else (0.0 if ship.get("free_expedited_shipping") else None),
            condition=cond,
            seller_name=it.get("shop_name"),
            image_url=(((photos[0].get("_links") or {}).get("small_crop") or {}).get("href")) if photos else None,
            description=it.get("description", "")[:300] if isinstance(it.get("description"), str) else "",
        ))
    return out


class ReverbSource(Source):
    key = "reverb"
    name = "Reverb"
    default_daily_limit = 10000     # conservative guess

    def __init__(self) -> None:
        self.token = os.getenv("REVERB_TOKEN", "")

    def configured(self) -> bool:
        # Listing search reportedly works without a token; REVERB_ENABLED=1 turns it on keyless.
        return bool(self.token) or os.getenv("REVERB_ENABLED") == "1"

    async def search(self, watch: Watch, client: httpx.AsyncClient) -> list[Listing]:
        headers = {"Accept": "application/hal+json", "Accept-Version": "3.0", "X-Display-Currency": "USD"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        params = {"query": watch.query, "per_page": "50"}
        if watch.max_price:
            params["price_max"] = f"{watch.max_price:.0f}"
        r = await client.get(API, headers=headers, params=params)
        r.raise_for_status()
        return parse_listings(r.json())
