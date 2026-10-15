"""Total cost, scam heuristics, "best value", and "is this a deal?"."""
from __future__ import annotations

import statistics
from typing import Iterable, Optional

from .models import Listing, ScoredListing, Watch

# When a shipped listing doesn't state shipping, assume this.
DEFAULT_SHIPPING_ESTIMATE = 15.0
# Round-trip driving cost per mile of distance, for local pickups.
TRAVEL_COST_PER_MILE = 0.67 * 2 * 0.5   # IRS rate, round trip, ~half counts as "real" cost

# How much a dollar spent on each condition is "worth" relative to new.
# Used only for best-value ranking, never for the displayed total.
CONDITION_FACTOR = {
    "new": 1.00,
    "open_box": 0.95,
    "refurbished": 0.90,
    "used": 0.85,
    "parts": 0.30,
}

SCAM_PHRASES = [
    "zelle only", "cashapp only", "cash app only", "venmo only", "wire transfer",
    "gift card", "western union", "shipping only", "ship only", "no local",
    "text me", "email me", "contact me at", "whatsapp", "deposit required",
    "send money", "paypal friends", "friends and family",
]

RISK_HIDE_THRESHOLD = 50


def condition_bucket(condition: str) -> str:
    """Price history is tracked separately for new-ish and used-ish items."""
    return "new" if condition in ("new", "open_box") else "used"


def total_cost(listing: Listing, watch: Watch) -> tuple[float, float, bool]:
    """Return (total, tax, shipping_was_estimated)."""
    estimated = False
    if listing.local_pickup:
        shipping = 0.0
    elif listing.shipping is None:
        shipping = DEFAULT_SHIPPING_ESTIMATE
        estimated = True
    else:
        shipping = listing.shipping
    tax = round(listing.price * watch.tax_rate, 2) if listing.taxable else 0.0
    return round(listing.price + shipping + tax, 2), tax, estimated


def assess_risk(listing: Listing, reference_median: Optional[float], total: float) -> tuple[int, list[str]]:
    score = 0
    flags: list[str] = []

    if reference_median and total < reference_median * 0.55:
        score += 45
        flags.append(f"price {int(100 - total / reference_median * 100)}% below typical")
    elif reference_median and total < reference_median * 0.7:
        score += 20
        flags.append("suspiciously cheap")

    fb = listing.seller_feedback_count
    if fb is not None and fb < 5:
        score += 25
        flags.append("brand-new seller")
    elif fb is not None and fb < 25:
        score += 10
        flags.append("low seller feedback")

    if listing.seller_rating is not None and listing.seller_rating < 95:
        score += 20
        flags.append(f"seller rating {listing.seller_rating:g}%")

    text = f"{listing.title} {listing.description}".lower()
    hits = [p for p in SCAM_PHRASES if p in text]
    if hits:
        score += 35
        flags.append("payment red flag: " + ", ".join(hits[:2]))

    return min(score, 100), flags


def deal_rating(total: float, median: Optional[float], sample_size: int) -> Optional[dict]:
    if not median or sample_size < 3:
        return None
    pct = (total - median) / median * 100
    if pct <= -15:
        label = "great"
    elif pct <= -5:
        label = "good"
    elif pct < 5:
        label = "fair"
    else:
        label = "high"
    return {"label": label, "pct_vs_median": round(pct, 1), "median": round(median, 2), "n": sample_size}


def score(listing: Listing, watch: Watch, medians: dict[str, tuple[float, int]]) -> ScoredListing:
    total, tax, estimated = total_cost(listing, watch)
    bucket = condition_bucket(listing.condition)
    median, n = medians.get(bucket, (None, 0))
    risk, flags = assess_risk(listing, median if n >= 3 else None, total)

    travel = (listing.distance_miles or 0) * TRAVEL_COST_PER_MILE if listing.local_pickup else 0
    effective = total / CONDITION_FACTOR.get(listing.condition, 0.85) + travel
    effective *= 1 + risk / 200   # a risk score of 100 makes it look 50% pricier
    if estimated:
        effective += 5            # small penalty for unknown shipping

    deal = deal_rating(total, median, n)
    if deal and risk >= RISK_HIDE_THRESHOLD:
        deal["label"] = "suspicious"   # too good to be true

    return ScoredListing(
        listing=listing,
        total_cost=total,
        shipping_estimated=estimated,
        tax=tax,
        effective_cost=round(effective, 2),
        risk_score=risk,
        risk_flags=flags,
        deal=deal,
    )


def median_of(values: Iterable[float]) -> tuple[Optional[float], int]:
    vals = list(values)
    if not vals:
        return None, 0
    return statistics.median(vals), len(vals)
