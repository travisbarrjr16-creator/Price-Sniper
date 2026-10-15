"""Core data types shared by sources, the watcher, and the API."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional

# Normalized condition buckets. Every source maps its own vocabulary onto these.
CONDITIONS = ("new", "open_box", "refurbished", "used", "parts")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Listing:
    """One listing from one marketplace, normalized."""

    source: str                      # display name, e.g. "eBay", "Best Buy"
    source_id: str                   # stable id within the source
    title: str
    url: str
    price: float                     # item price (or current bid)
    condition: str = "used"          # one of CONDITIONS
    shipping: Optional[float] = None  # None = unknown
    currency: str = "USD"
    seller_name: Optional[str] = None
    seller_rating: Optional[float] = None       # positive-feedback percent, 0-100
    seller_feedback_count: Optional[int] = None
    location: Optional[str] = None
    distance_miles: Optional[float] = None
    local_pickup: bool = False
    taxable: bool = True             # private local sales usually aren't taxed
    is_auction: bool = False
    image_url: Optional[str] = None
    description: str = ""            # snippet, used for scam heuristics
    listed_at: Optional[datetime] = None
    source_key: str = ""             # adapter key ("ebay", "walmart"...), set by the watcher

    @property
    def key(self) -> str:
        return f"{self.source}:{self.source_id}"


@dataclass
class Watch:
    """A saved search that runs continuously in the background."""

    query: str
    max_price: Optional[float] = None            # compared against total cost
    conditions: list[str] = field(default_factory=lambda: ["new", "open_box", "refurbished", "used"])
    zip_code: Optional[str] = None
    radius_miles: Optional[int] = None           # for local-pickup listings
    required_terms: list[str] = field(default_factory=list)   # e.g. ["8gb"]
    excluded_terms: list[str] = field(default_factory=list)   # e.g. ["laptop"]
    sources: list[str] = field(default_factory=list)          # empty = all configured
    tax_rate: float = 0.0                        # e.g. 0.0825
    interval_minutes: int = 15
    strict_variants: bool = True                 # "4060" must not match "4060 Ti"
    hide_risky: bool = True
    include_auctions: bool = False
    active: bool = True
    id: Optional[int] = None
    user_id: Optional[int] = None
    created_at: Optional[str] = None
    last_checked_at: Optional[str] = None
    last_error: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ScoredListing:
    listing: Listing
    total_cost: float
    shipping_estimated: bool
    tax: float
    effective_cost: float            # total adjusted for condition, risk, travel
    risk_score: int                  # 0-100
    risk_flags: list[str]
    deal: Optional[dict] = None      # {"label", "pct_vs_median", "median"}


@dataclass
class User:
    id: int
    email: str
    ntfy_topic: Optional[str] = None
    discord_webhook: Optional[str] = None
    gmail_email: Optional[str] = None   # set when Gmail is connected

    def public(self) -> dict:
        return {"id": self.id, "email": self.email, "ntfy_topic": self.ntfy_topic,
                "discord_webhook": self.discord_webhook, "gmail_email": self.gmail_email}
