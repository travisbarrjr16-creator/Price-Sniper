from __future__ import annotations

import os
import re
from abc import ABC, abstractmethod

import httpx

from ..models import Listing, Watch


# The .env settings for each site, named in "check your keys" messages
KEY_SETTINGS = {
    "ebay": "EBAY_CLIENT_ID / EBAY_CLIENT_SECRET", "bestbuy": "BESTBUY_API_KEY", "walmart": "WALMART_CONSUMER_ID",
    "reddit": "REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET", "etsy": "ETSY_API_KEY / ETSY_SHARED_SECRET",
    "reverb": "REVERB_TOKEN", "gmail": "GMAIL_CLIENT_ID / GMAIL_CLIENT_SECRET",
}


def scrub(text: str) -> str:
    """Remove query strings from URLs: some sites (Best Buy) put the server's API key there."""
    return re.sub(r"(https?://[^\s'\"?]+)\?[^\s'\"]*", r"\1?…", str(text))


def friendly_error(source: "Source | None", exc: BaseException) -> str:
    """A short, plain message users can see on their watch. Never contains keys."""
    name = source.name if source else "The site"
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code in (400, 401, 403) and source and source.key in KEY_SETTINGS:
            return (f"{name} turned down the server's keys (HTTP {code}). Server owner: check "
                    f"{KEY_SETTINGS[source.key]} in .env, then run: python -m sniper.admin test-sources")
        if code == 429:
            return f"{name} says we're asking too often right now; trying again next check"
        if code >= 500:
            return f"{name} is having problems on their end (HTTP {code}); trying again next check"
        return f"{name} refused the search (HTTP {code})"
    if isinstance(exc, httpx.TimeoutException):
        return f"{name} didn't answer in time; trying again next check"
    if isinstance(exc, httpx.HTTPError):
        return f"couldn't reach {name} ({type(exc).__name__}); trying again next check"
    return scrub(str(exc) or type(exc).__name__)[:300]


class Source(ABC):
    """A marketplace adapter. Implement `search` and register it in sources/__init__.py."""

    key: str = ""          # short id used in Watch.sources, e.g. "ebay"
    name: str = ""         # display name
    # Requests per day this server's key may make. All users share it.
    # Override in .env with SNIPER_LIMIT_<KEY>=n (0 = no limit) once you know your real quota.
    default_daily_limit: int | None = None

    def daily_limit(self) -> int | None:
        raw = os.getenv(f"SNIPER_LIMIT_{self.key.upper()}")
        if raw is not None and raw.strip().isdigit():
            return int(raw) or None
        return self.default_daily_limit

    def calls_per_check(self, watch: Watch) -> int:
        """Rough number of requests one check of this watch makes (for planning the daily budget)."""
        return 1

    @abstractmethod
    def configured(self) -> bool:
        """True when the credentials this source needs are present."""

    @abstractmethod
    async def search(self, watch: Watch, client: httpx.AsyncClient) -> list[Listing]:
        """Return raw candidate listings. Strict matching happens later."""
