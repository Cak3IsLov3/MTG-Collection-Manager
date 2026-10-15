"""Commander Spellbook (commanderspellbook.com): which combos a list of cards contains."""

import asyncio
import logging
import time

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

BASE_URL = "https://backend.commanderspellbook.com"


class SpellbookError(Exception):
    pass


class SpellbookClient:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None, min_interval: float = 1.0) -> None:
        self._client = httpx.AsyncClient(
            base_url=BASE_URL, timeout=60.0, transport=transport,
            headers={"User-Agent": get_settings().user_agent, "Accept": "application/json"},
        )
        self._min_interval = min_interval
        self._last = 0.0
        self._lock = asyncio.Lock()

    async def find_combos(self, cards: list[str], commanders: list[str]) -> dict:
        """The combos in a deck: Commander Spellbook's "find my combos" result (included,
        almostIncluded, ...)."""
        payload = {
            "main": [{"card": name, "quantity": 1} for name in cards],
            "commanders": [{"card": name, "quantity": 1} for name in commanders],
        }
        async with self._lock:  # one request at a time, a second apart
            wait = self._min_interval - (time.monotonic() - self._last)
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                response = await self._client.post("/find-my-combos", json=payload)
            except httpx.HTTPError as exc:
                raise SpellbookError(f"Commander Spellbook could not be reached: {exc}") from exc
            finally:
                self._last = time.monotonic()
        if response.status_code >= 400:
            raise SpellbookError(f"Commander Spellbook answered HTTP {response.status_code}")
        return response.json().get("results") or {}

    async def aclose(self) -> None:
        await self._client.aclose()
