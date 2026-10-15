from __future__ import annotations

import os
from abc import ABC, abstractmethod

import httpx

from ..models import Listing, Watch


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
