"""EDHREC (edhrec.com): which cards Commander decks with a commander play most."""

import asyncio
import datetime as dt
import re
import time
import unicodedata

import httpx

from app.config import get_settings

BASE_URL = "https://json.edhrec.com"
SALT_FLOOR = 1.0  # cards EDHREC's players rate below this are not really salty
SALT_PAGES = 12  # at most this many pages of 100 cards of the salt list
SALT_FOR = 7 * 24 * 3600  # the salt list is fetched again after a week
SALT_RETRY = 600  # after a failure, not again for ten minutes


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
        self._salt: dict[str, float] | None = None  # card name (casefolded) -> salt score
        self._salt_at = 0.0
        self._salt_task: asyncio.Task | None = None
        self._salt_failed_at: float | None = None
        self.salt_error: str | None = None  # why the salt list could not be fetched the last time

    async def _get(self, path: str) -> httpx.Response:
        async with self._lock:  # one request at a time, a second apart
            wait = self._min_interval - (time.monotonic() - self._last)
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                return await self._client.get(path)
            except httpx.HTTPError as exc:
                raise EdhrecError(f"EDHREC could not be reached: {exc}") from exc
            finally:
                self._last = time.monotonic()

    async def salt_scores(self) -> dict[str, float]:
        """How salty EDHREC's players find cards (0 to 4: how much they hate playing against it),
        for the cards from SALT_FLOOR up; fetched at most once a week."""
        if self._salt is not None and time.monotonic() - self._salt_at < SALT_FOR:
            return self._salt
        scores: dict[str, float] = {}
        path = "/pages/top/salt.json"
        for _ in range(SALT_PAGES):
            response = await self._get(path)
            if response.status_code >= 400:
                if scores:
                    break  # the pages after this one: the list so far will do
                raise EdhrecError(f"EDHREC answered HTTP {response.status_code} for the salt list")
            page = response.json()
            lists = (page.get("container") or {}).get("json_dict", {}).get("cardlists") or [{}]
            views = page.get("cardviews") or lists[0].get("cardviews") or []
            for view in views:
                if view.get("name") and view.get("salt") is not None:
                    scores.setdefault(view["name"].split(" // ")[0].casefold(), round(float(view["salt"]), 2))
            more = page.get("more") or lists[0].get("more")
            if not views or not more or min(view.get("salt") or 0 for view in views) < SALT_FLOOR:
                break
            path = f"/pages/{more}"
        self._salt = {name: salt for name, salt in scores.items() if salt >= SALT_FLOOR}
        self.salt_error = self._salt_failed_at = None
        self._salt_at = time.monotonic()
        return self._salt

    async def salt_scores_soon(self, wait: float = 3.0) -> dict[str, float] | None:
        """The salt scores, waiting at most `wait` seconds for them; None while they are still being
        fetched (a dozen pages, a second apart), they are then ready on a next visit."""
        if self._salt is not None and time.monotonic() - self._salt_at < SALT_FOR:
            return self._salt
        if self._salt_failed_at is not None and time.monotonic() - self._salt_failed_at < SALT_RETRY:
            return self._salt  # it failed a moment ago: not on every page view
        if self._salt_task is None or self._salt_task.done():
            self._salt_task = asyncio.create_task(self.salt_scores())
        try:
            return await asyncio.wait_for(asyncio.shield(self._salt_task), wait)
        except asyncio.TimeoutError:
            return self._salt  # the list of last week, if there is one
        except EdhrecError as exc:
            self.salt_error, self._salt_failed_at = str(exc), time.monotonic()
            return self._salt

    async def commander(self, name: str) -> dict:
        """The EDHREC page of a commander (raw JSON), fetched at most once a day."""
        slug = commander_slug(name)
        cached = self._cache.get(slug)
        if cached and cached[0] == dt.date.today():
            return cached[1]
        response = await self._get(f"/pages/commanders/{slug}.json")
        if response.status_code == 404:
            raise EdhrecError(f"EDHREC has no page for {name}")
        if response.status_code >= 400:
            raise EdhrecError(f"EDHREC answered HTTP {response.status_code}")
        page = response.json()
        self._cache[slug] = (dt.date.today(), page)
        return page

    async def aclose(self) -> None:
        await self._client.aclose()
