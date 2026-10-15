"""Walmart via the Walmart I/O Affiliate Product API (walmart.io, free account).

Requests are signed with an RSA key you generate and upload to walmart.io.
"""
from __future__ import annotations

import base64
import os
import time
from pathlib import Path

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from ..models import Listing, Watch
from .base import Source

SEARCH_URL = "https://developer.api.walmart.com/api-proxy/service/affil/product/v2/search"


def sign_headers(consumer_id: str, key_version: str, private_key_pem: bytes, ts_ms: int | None = None) -> dict:
    ts = str(ts_ms if ts_ms is not None else int(time.time() * 1000))
    # Walmart signs the header values, sorted by header name, each followed by "\n".
    to_sign = f"{consumer_id}\n{ts}\n{key_version}\n".encode()
    key = serialization.load_pem_private_key(private_key_pem, password=None)
    sig = key.sign(to_sign, padding.PKCS1v15(), hashes.SHA256())
    return {
        "WM_CONSUMER.ID": consumer_id,
        "WM_CONSUMER.INTIMESTAMP": ts,
        "WM_SEC.KEY_VERSION": key_version,
        "WM_SEC.AUTH_SIGNATURE": base64.b64encode(sig).decode(),
    }


def parse_items(items: list[dict]) -> list[Listing]:
    out = []
    for it in items:
        price = it.get("salePrice") or it.get("msrp")
        if price is None:
            continue
        price = float(price)
        name = it.get("name", "")
        ship = it.get("standardShipRate")
        if it.get("freeShippingOver35Dollars") and price >= 35:
            ship = 0.0
        seller = (it.get("sellerInfo") or "Walmart").strip()
        lower = name.lower()
        cond = "refurbished" if ("restored" in lower or "refurbished" in lower) else "new"
        out.append(Listing(
            source="Walmart",
            source_id=str(it["itemId"]),
            title=name,
            url=it.get("productTrackingUrl") or it.get("productUrl") or f"https://www.walmart.com/ip/{it['itemId']}",
            price=price,
            shipping=float(ship) if ship is not None else None,
            condition=cond,
            seller_name=seller,
            # Walmart itself is a known-good seller; marketplace sellers have no public rating here.
            seller_rating=100.0 if seller.lower().startswith("walmart") else None,
            seller_feedback_count=100000 if seller.lower().startswith("walmart") else None,
            image_url=it.get("mediumImage") or it.get("thumbnailImage"),
        ))
    return out


class WalmartSource(Source):
    key = "walmart"
    name = "Walmart"
    default_daily_limit = 5000      # conservative guess; set SNIPER_LIMIT_WALMART to your real quota

    def __init__(self) -> None:
        self.consumer_id = os.getenv("WALMART_CONSUMER_ID", "")
        self.key_version = os.getenv("WALMART_KEY_VERSION", "1")
        self.key_path = os.getenv("WALMART_PRIVATE_KEY_PATH", "")

    def configured(self) -> bool:
        return bool(self.consumer_id and self.key_path and Path(self.key_path).exists())

    async def search(self, watch: Watch, client: httpx.AsyncClient) -> list[Listing]:
        headers = sign_headers(self.consumer_id, self.key_version, Path(self.key_path).read_bytes())
        params = {"query": watch.query, "numItems": "25"}
        if os.getenv("WALMART_PUBLISHER_ID"):
            params["publisherId"] = os.environ["WALMART_PUBLISHER_ID"]
        r = await client.get(SEARCH_URL, headers=headers, params=params)
        r.raise_for_status()
        return parse_items(r.json().get("items", []))
