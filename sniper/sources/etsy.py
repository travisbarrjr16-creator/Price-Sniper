"""Etsy via Open API v3 (free app key from etsy.com/developers)."""
from __future__ import annotations

import os

import httpx

from ..models import Listing, Watch
from .base import Source

API = "https://openapi.etsy.com/v3/application/listings/active"


def parse_results(data: dict) -> list[Listing]:
    out = []
    for it in data.get("results", []):
        p = it.get("price") or {}
        try:
            price = p["amount"] / (p.get("divisor") or 100)
        except (KeyError, TypeError):
            continue
        title = it.get("title", "")
        low = title.lower()
        cond = "used" if any(w in low for w in ("vintage", "used", "pre-owned", "preowned")) else "new"
        imgs = it.get("images") or []
        out.append(Listing(
            source="Etsy",
            source_id=str(it["listing_id"]),
            title=title,
            url=it.get("url") or f"https://www.etsy.com/listing/{it['listing_id']}",
            price=round(price, 2),
            shipping=None,     # shipping profiles need a second call per listing; estimated instead
            condition=cond,
            image_url=imgs[0].get("url_170x135") if imgs else None,
            description=(it.get("description") or "")[:300],
        ))
    return out


class EtsySource(Source):
    key = "etsy"
    name = "Etsy"
    default_daily_limit = 10000

    def __init__(self) -> None:
        self.api_key = os.getenv("ETSY_API_KEY", "")
        self.shared_secret = os.getenv("ETSY_SHARED_SECRET", "")

    def configured(self) -> bool:
        return bool(self.api_key)

    async def search(self, watch: Watch, client: httpx.AsyncClient) -> list[Listing]:
        # Newer Etsy apps send "keystring:shared_secret"; older ones just the keystring.
        key = f"{self.api_key}:{self.shared_secret}" if self.shared_secret else self.api_key
        params = {"keywords": watch.query, "limit": "100", "sort_on": "price", "sort_order": "asc", "includes": "Images"}
        if watch.max_price:
            params["max_price"] = f"{watch.max_price:.2f}"
        r = await client.get(API, headers={"x-api-key": key}, params=params)
        r.raise_for_status()
        return parse_results(r.json())
