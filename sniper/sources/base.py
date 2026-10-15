from __future__ import annotations

from abc import ABC, abstractmethod

import httpx

from ..models import Listing, Watch


class Source(ABC):
    """A marketplace adapter. Implement `search` and register it in sources/__init__.py."""

    key: str = ""          # short id used in Watch.sources, e.g. "ebay"
    name: str = ""         # display name

    @abstractmethod
    def configured(self) -> bool:
        """True when the credentials this source needs are present."""

    @abstractmethod
    async def search(self, watch: Watch, client: httpx.AsyncClient) -> list[Listing]:
        """Return raw candidate listings. Strict matching happens later."""
