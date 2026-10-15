"""Best Buy via the official Products API (developer.bestbuy.com)."""
from __future__ import annotations

import os

import httpx

from ..matching import tokens
from ..models import Listing, Watch
from .base import Source

API = "https://api.bestbuy.com/v1/products"
CONDITION_MAP = {"new": "new", "refurbished": "refurbished", "pre-owned": "used"}


class BestBuySource(Source):
    key = "bestbuy"
    name = "Best Buy"

    def __init__(self) -> None:
        self.api_key = os.getenv("BESTBUY_API_KEY", "")

    def configured(self) -> bool:
        return bool(self.api_key)

    async def search(self, watch: Watch, client: httpx.AsyncClient) -> list[Listing]:
        terms = "&".join(f"search={t}" for t in tokens(watch.query))
        params = {
            "apiKey": self.api_key,
            "format": "json",
            "pageSize": "100",
            "show": "sku,name,salePrice,url,image,condition,freeShipping,shippingCost,onlineAvailability",
        }
        r = await client.get(f"{API}(({terms})&onlineAvailability=true)", params=params)
        r.raise_for_status()
        out = []
        for p in r.json().get("products", []):
            shipping = 0.0 if p.get("freeShipping") else p.get("shippingCost")
            out.append(Listing(
                source=self.name,
                source_id=str(p["sku"]),
                title=p.get("name", ""),
                url=p.get("url", ""),
                price=float(p.get("salePrice") or 0),
                shipping=float(shipping) if shipping not in (None, "") else None,
                condition=CONDITION_MAP.get(str(p.get("condition", "new")).lower(), "new"),
                seller_name="Best Buy",
                seller_rating=100.0,
                seller_feedback_count=100000,
                image_url=p.get("image"),
            ))
        return out
