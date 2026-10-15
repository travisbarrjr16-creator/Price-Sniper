"""Push notifications. ntfy.sh is the easiest path to your phone (free app, no account)."""
from __future__ import annotations

import logging
import os

import httpx

log = logging.getLogger("sniper.alerts")


class Notifier:
    def __init__(self) -> None:
        self.ntfy_topic = os.getenv("NTFY_TOPIC", "")
        self.ntfy_server = os.getenv("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
        self.ntfy_token = os.getenv("NTFY_TOKEN", "")
        self.discord_webhook = os.getenv("DISCORD_WEBHOOK_URL", "")

    def channels(self) -> list[str]:
        return [n for n, on in (("ntfy", self.ntfy_topic), ("discord", self.discord_webhook)) if on]

    async def send(self, client: httpx.AsyncClient, title: str, message: str, url: str, urgent: bool = False) -> None:
        log.info("ALERT %s | %s | %s", title, message, url)
        if self.ntfy_topic:
            headers = {"Authorization": f"Bearer {self.ntfy_token}"} if self.ntfy_token else {}
            try:
                await client.post(self.ntfy_server, headers=headers, json={
                    "topic": self.ntfy_topic,
                    "title": title,
                    "message": message,
                    "click": url,                       # tap the notification -> opens the listing
                    "actions": [{"action": "view", "label": "Open listing", "url": url}],
                    "tags": ["moneybag"],
                    "priority": 5 if urgent else 4,
                })
            except httpx.HTTPError as e:
                log.warning("ntfy failed: %s", e)
        if self.discord_webhook:
            try:
                await client.post(self.discord_webhook, json={"content": f"**{title}**\n{message}\n{url}"})
            except httpx.HTTPError as e:
                log.warning("discord failed: %s", e)
