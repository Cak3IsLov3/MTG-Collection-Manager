import logging

import httpx

from app.models import Card

logger = logging.getLogger(__name__)


class DiscordNotifier:
    def __init__(self, webhook_url: str | None, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.webhook_url = webhook_url
        self._transport = transport

    @property
    def configured(self) -> bool:
        return bool(self.webhook_url)

    async def send_price_rises(self, lines: list[str], days: int) -> bool:
        """Post one message listing cards you own that went up; False (never raises) when delivery fails."""
        if not self.webhook_url or not lines:
            return False
        shown = lines[:15]
        more = len(lines) - len(shown)
        embed = {
            "title": f"{len(lines)} card{'s' if len(lines) != 1 else ''} you own went up in price",
            "description": "\n".join(shown) + (f"\n… and {more} more" if more else "")
            + f"\n\nCompared with {days} days ago.",
            "color": 0xE8A33D,
        }
        return await self._post({"embeds": [embed]})

    async def _post(self, payload: dict) -> bool:
        try:
            async with httpx.AsyncClient(timeout=10.0, transport=self._transport) as client:
                response = await client.post(self.webhook_url, json=payload)
        except httpx.HTTPError as exc:
            logger.warning("Discord webhook failed: %s", exc)
            return False
        if response.status_code >= 300:
            logger.warning("Discord webhook returned HTTP %s", response.status_code)
            return False
        return True

    async def send_price_alert(
        self, card: Card, price: float, target: float, url: str | None, source: str
    ) -> bool:
        """Post a price alert; returns False (never raises) when delivery fails."""
        if not self.webhook_url:
            return False
        embed = {
            "title": f"{card.name} is now €{price:.2f}",
            "description": (
                f"**{card.set_name}** ({card.set_code.upper()})\n"
                f"Your target: €{target:.2f} · Source: {source}"
            ),
            "color": 0x2ECC71,
        }
        if url:
            embed["url"] = url
        if card.image_url:
            embed["thumbnail"] = {"url": card.image_url}
        try:
            async with httpx.AsyncClient(timeout=10.0, transport=self._transport) as client:
                response = await client.post(self.webhook_url, json={"embeds": [embed]})
        except httpx.HTTPError as exc:
            logger.warning("Discord webhook failed: %s", exc)
            return False
        if response.status_code >= 300:
            logger.warning("Discord webhook returned HTTP %s", response.status_code)
            return False
        return True
