from __future__ import annotations

import os

from .base import Source
from .bestbuy import BestBuySource
from .demo import DemoSource
from .ebay import EbaySource
from .etsy import EtsySource
from .gmail import GmailAlertsSource
from .reverb import ReverbSource
from .walmart import WalmartSource

# To add a marketplace: subclass Source and append it here.
ALL_SOURCES: list[Source] = [
    EbaySource(), BestBuySource(), WalmartSource(), ReverbSource(), EtsySource(),
    GmailAlertsSource(), DemoSource(),
]


def get_source(key: str) -> Source | None:
    return next((s for s in ALL_SOURCES if s.key == key), None)


def enabled_sources() -> list[Source]:
    """Real sources with credentials; the demo source if asked for, or if nothing else is set up."""
    real = [s for s in ALL_SOURCES if s.key != "demo" and s.configured()]
    demo_flag = os.getenv("SNIPER_DEMO", "")
    if demo_flag == "1" or (not real and demo_flag != "0"):
        real += [s for s in ALL_SOURCES if s.key == "demo"]
    return real


def site_of(source_name: str) -> str:
    """'eBay Local' and 'eBay' are the same site for filtering."""
    return source_name.removesuffix(" Local") if source_name != "Demo Local" else source_name


__all__ = ["Source", "ALL_SOURCES", "enabled_sources", "get_source", "site_of"]
