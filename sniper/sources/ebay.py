"""eBay via the official Browse API (free developer keys: developer.ebay.com).

Covers new, used, refurbished, and local-pickup listings near a ZIP code.
"""
from __future__ import annotations

import base64
import os
import time
from datetime import datetime
from urllib.parse import quote

import httpx

from ..models import Listing, Watch
from .base import Source

TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
SEARCH_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"

# eBay condition ids -> our buckets
CONDITION_IDS = {
    "new": ["1000"],
    "open_box": ["1500", "1750", "2750"],
    "refurbished": ["2000", "2010", "2020", "2030", "2500"],
    "used": ["3000", "4000", "5000", "6000"],
    "parts": ["7000"],
}
_ID_TO_CONDITION = {cid: cond for cond, ids in CONDITION_IDS.items() for cid in ids}


def _float(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


class EbaySource(Source):
    key = "ebay"
    name = "eBay"
    default_daily_limit = 5000      # Browse API default for a new app; check your eBay developer dashboard

    def __init__(self) -> None:
        self.client_id = os.getenv("EBAY_CLIENT_ID", "")
        self.client_secret = os.getenv("EBAY_CLIENT_SECRET", "")
        self._token: str | None = None
        self._token_expiry = 0.0

    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret)

    def calls_per_check(self, watch: Watch) -> int:
        return 2 if (watch.zip_code and watch.radius_miles) else 1     # + local-pickup search

    async def _get_token(self, client: httpx.AsyncClient) -> str:
        if self._token and time.time() < self._token_expiry - 60:
            return self._token
        basic = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode()
        r = await client.post(
            TOKEN_URL,
            headers={"Authorization": f"Basic {basic}", "Content-Type": "application/x-www-form-urlencoded"},
            data={"grant_type": "client_credentials", "scope": "https://api.ebay.com/oauth/api_scope"},
        )
        r.raise_for_status()
        body = r.json()
        self._token = body["access_token"]
        self._token_expiry = time.time() + int(body.get("expires_in", 7200))
        return self._token

    def _filters(self, watch: Watch, local: bool) -> str:
        parts = ["priceCurrency:USD"]
        if watch.max_price:
            parts.append(f"price:[..{watch.max_price:.0f}]")
        cond_ids = [cid for c in watch.conditions for cid in CONDITION_IDS.get(c, [])]
        if cond_ids:
            parts.append("conditionIds:{" + "|".join(cond_ids) + "}")
        if not watch.include_auctions:
            parts.append("buyingOptions:{FIXED_PRICE|BEST_OFFER}")
        if local:
            parts += [
                "deliveryOptions:{SELLER_ARRANGED_LOCAL_PICKUP}",
                "pickupCountry:US",
                f"pickupPostalCode:{watch.zip_code}",
                f"pickupRadius:{watch.radius_miles}",
                "pickupRadiusUnit:mi",
            ]
        return ",".join(parts)

    async def _query(self, watch: Watch, client: httpx.AsyncClient, local: bool) -> list[dict]:
        token = await self._get_token(client)
        headers = {"Authorization": f"Bearer {token}", "X-EBAY-C-MARKETPLACE-ID": "EBAY_US"}
        if watch.zip_code:
            # lets eBay compute shipping cost to the user's ZIP
            # eBay expects the inner value URL-encoded: contextualLocation=country%3DUS%2Czip%3D78701
            headers["X-EBAY-C-ENDUSERCTX"] = "contextualLocation=" + quote(f"country=US,zip={watch.zip_code}", safe="")
        params = {"q": watch.query, "limit": "200", "filter": self._filters(watch, local), "sort": "price"}
        r = await client.get(SEARCH_URL, headers=headers, params=params)
        if r.status_code == 401:            # token revoked or expired early: get a fresh one and retry once
            self._token = None
            headers["Authorization"] = f"Bearer {await self._get_token(client)}"
            r = await client.get(SEARCH_URL, headers=headers, params=params)
        r.raise_for_status()
        return r.json().get("itemSummaries", [])

    def _to_listing(self, it: dict, local: bool) -> Listing:
        price = _float((it.get("price") or {}).get("value")) or 0.0
        is_auction = "AUCTION" in it.get("buyingOptions", []) and "FIXED_PRICE" not in it.get("buyingOptions", [])
        if is_auction and it.get("currentBidPrice"):
            price = _float(it["currentBidPrice"].get("value")) or price

        shipping = None
        for opt in it.get("shippingOptions", []):
            cost = _float((opt.get("shippingCost") or {}).get("value"))
            if cost is not None:
                shipping = cost if shipping is None else min(shipping, cost)

        seller = it.get("seller") or {}
        loc = it.get("itemLocation") or {}
        dist = it.get("distanceFromPickupLocation") or {}
        listed = it.get("itemCreationDate")
        return Listing(
            source=self.name + (" Local" if local else ""),
            source_id=it["itemId"],
            title=it.get("title", ""),
            url=it.get("itemWebUrl", ""),
            price=price,
            shipping=shipping,
            condition=_ID_TO_CONDITION.get(str(it.get("conditionId")), "used"),
            seller_name=seller.get("username"),
            seller_rating=_float(seller.get("feedbackPercentage")),
            seller_feedback_count=int(seller["feedbackScore"]) if seller.get("feedbackScore") is not None else None,
            location=", ".join(x for x in (loc.get("city"), loc.get("stateOrProvince"), loc.get("postalCode")) if x) or None,
            distance_miles=_float(dist.get("value")),
            local_pickup=local,
            taxable=True,   # eBay collects sales tax on every order
            is_auction=is_auction,
            image_url=(it.get("image") or {}).get("imageUrl"),
            description=it.get("shortDescription", "") or "",
            # one listing with several options (e.g. a GPU and a $5 cable): the shown price is just one option
            price_varies=bool(it.get("itemGroupType") or it.get("itemGroupHref")),
            listed_at=datetime.fromisoformat(listed.replace("Z", "+00:00")) if listed else None,
        )

    async def search(self, watch: Watch, client: httpx.AsyncClient) -> list[Listing]:
        out = [self._to_listing(it, local=False) for it in await self._query(watch, client, local=False)]
        if watch.zip_code and watch.radius_miles:
            seen = {l.source_id for l in out}
            for it in await self._query(watch, client, local=True):
                if it["itemId"] not in seen:
                    out.append(self._to_listing(it, local=True))
        return out
