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

    async def send_price_drops(self, lines: list[str], days: int) -> bool:
        """Post one message listing wishlist cards that got cheaper; False (never raises) when delivery fails."""
        if not self.webhook_url or not lines:
            return False
        shown = lines[:15]
        more = len(lines) - len(shown)
        embed = {
            "title": f"{len(lines)} card{'s' if len(lines) != 1 else ''} on your wishlist got cheaper",
            "description": "\n".join(shown) + (f"\n… and {more} more" if more else "")
            + f"\n\nCompared with {days} days ago.",
            "color": 0x3ECF8E,
        }
        return await self._post({"embeds": [embed]})

    async def send_reprints(self, lines: list[str]) -> bool:
        """One message listing reprints of valuable cards you own; False (never raises) when delivery fails."""
        if not self.webhook_url or not lines:
            return False
        shown = lines[:15]
        more = len(lines) - len(shown)
        embed = {
            "title": f"{len(lines)} card{'s' if len(lines) != 1 else ''} you own got reprinted",
            "description": "\n".join(shown) + (f"\n… and {more} more" if more else "")
            + "\n\nA reprint often makes the price drop.",
            "color": 0xC77DFF,
        }
        return await self._post({"embeds": [embed]})

    async def send_deck_changes(self, changes: list[dict]) -> bool:
        """One message for decks that changed on Archidekt: {"name", "url", "lines"} each (at most ten
        are shown). False (never raises) when delivery fails."""
        if not self.webhook_url or not changes:
            return False
        embeds = [
            {"title": f"{change['name']} changed on Archidekt", "description": "\n".join(change["lines"])[:4000],
             "color": 0x9B6DFF, **({"url": change["url"]} if change.get("url") else {})}
            for change in changes[:10]
        ]
        return await self._post({"embeds": embeds})

    async def send_weekly_summary(self, lines: list[str], period: str) -> bool:
        """Post the weekly summary; False (never raises) when delivery fails."""
        if not self.webhook_url:
            return False
        embed = {"title": f"Your week in Magic · {period}", "description": "\n".join(lines), "color": 0x4C9AFF}
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
