"""Push notifications, per user. ntfy is the easiest path to a phone (free app, no account)."""
from __future__ import annotations

import logging
import os
import re
from typing import Optional

import httpx

log = logging.getLogger("sniper.alerts")

NTFY_TOPIC_RE = re.compile(r"^[A-Za-z0-9_-]{6,64}$")
DISCORD_RE = re.compile(r"^https://(discord|discordapp)\.com/api/webhooks/\d+/[\w-]+$")


def valid_ntfy_topic(t: Optional[str]) -> bool:
    return t is None or bool(NTFY_TOPIC_RE.match(t))


def valid_discord_webhook(u: Optional[str]) -> bool:
    # Only real Discord webhook URLs, so users can't make the server POST anywhere they like.
    return u is None or bool(DISCORD_RE.match(u))


class Notifier:
    def __init__(self) -> None:
        self.ntfy_server = os.getenv("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
        self.ntfy_token = os.getenv("NTFY_TOKEN", "")

    async def send(self, client: httpx.AsyncClient, title: str, message: str, url: str, urgent: bool = False,
                   ntfy_topic: Optional[str] = None, discord_webhook: Optional[str] = None) -> None:
        log.info("ALERT %s | %s | %s", title, message.replace("\n", " / "), url)
        if ntfy_topic and valid_ntfy_topic(ntfy_topic):
            headers = {"Authorization": f"Bearer {self.ntfy_token}"} if self.ntfy_token else {}
            try:
                await client.post(self.ntfy_server, headers=headers, json={
                    "topic": ntfy_topic,
                    "title": title,
                    "message": message,
                    "click": url,                       # tap the notification -> opens the listing
                    "actions": [{"action": "view", "label": "Open listing", "url": url}],
                    "tags": ["moneybag"],
                    "priority": 5 if urgent else 4,
                })
            except httpx.HTTPError as e:
                log.warning("ntfy failed: %s", e)
        if discord_webhook and valid_discord_webhook(discord_webhook):
            try:
                await client.post(discord_webhook, json={"content": f"**{title}**\n{message}\n{url}"})
            except httpx.HTTPError as e:
                log.warning("discord failed: %s", e)
