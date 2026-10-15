"""EDHREC (edhrec.com): which cards Commander decks with a commander play most."""

import asyncio
import datetime as dt
import re
import time
import unicodedata

import httpx

from app.config import get_settings

BASE_URL = "https://json.edhrec.com"


class EdhrecError(Exception):
    pass


def commander_slug(name: str) -> str:
    """"Optimus Prime, Hero // Optimus Prime, Autobot Leader" -> "optimus-prime-hero": EDHREC's page name."""
    front = name.split(" // ")[0]
    plain = unicodedata.normalize("NFKD", front).encode("ascii", "ignore").decode()
    plain = re.sub(r"['’.,!?:\"]", "", plain.lower())
    return re.sub(r"[^a-z0-9]+", "-", plain).strip("-")


class EdhrecClient:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None, min_interval: float = 1.0) -> None:
        self._client = httpx.AsyncClient(
            base_url=BASE_URL, timeout=30.0, transport=transport, follow_redirects=True,
            headers={"User-Agent": get_settings().user_agent, "Accept": "application/json"},
        )
        self._min_interval = min_interval
        self._last = 0.0
        self._lock = asyncio.Lock()
        self._cache: dict[str, tuple[dt.date, dict]] = {}  # slug -> (day fetched, page)

    async def commander(self, name: str) -> dict:
        """The EDHREC page of a commander (raw JSON), fetched at most once a day."""
        slug = commander_slug(name)
        cached = self._cache.get(slug)
        if cached and cached[0] == dt.date.today():
            return cached[1]
        async with self._lock:  # one request at a time, a second apart
            wait = self._min_interval - (time.monotonic() - self._last)
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                response = await self._client.get(f"/pages/commanders/{slug}.json")
            except httpx.HTTPError as exc:
                raise EdhrecError(f"EDHREC could not be reached: {exc}") from exc
            finally:
                self._last = time.monotonic()
        if response.status_code == 404:
            raise EdhrecError(f"EDHREC has no page for {name}")
        if response.status_code >= 400:
            raise EdhrecError(f"EDHREC answered HTTP {response.status_code}")
        page = response.json()
        self._cache[slug] = (dt.date.today(), page)
        return page

    async def aclose(self) -> None:
        await self._client.aclose()
