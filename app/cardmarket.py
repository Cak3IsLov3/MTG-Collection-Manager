"""Cardmarket product page scraper.

Cardmarket has no public API, so the lowest listed price ("From") is parsed
from the product page. Their HTML changes regularly, which is why every
selector and label lives in cardmarket_selectors.json instead of in code.
"""

import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx
from bs4 import BeautifulSoup

from app.config import BASE_DIR, get_settings
from app.rate_limit import RateLimiter

logger = logging.getLogger(__name__)

SELECTORS_PATH = BASE_DIR / "cardmarket_selectors.json"

# When Cardmarket refuses this many requests in a row it is blocking us, not one page.
# Asking for the remaining cards would only take minutes (the rate limit) and risk a longer ban.
BLOCKED_LIMIT = 3
BLOCKED_PAUSE_SECONDS = 60 * 60

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}


class CardmarketError(Exception):
    pass


class CardmarketBlocked(CardmarketError):
    """Rate limited or stopped by bot protection."""


class CardmarketPaused(CardmarketBlocked):
    """Not even tried: Cardmarket refused several requests in a row, so we leave it alone for a while."""


class CardmarketParseError(CardmarketError):
    """Page loaded, but no price could be found (selectors probably outdated)."""


@dataclass
class CardmarketPrice:
    low: float | None
    trend: float | None


def load_selectors(path: Path = SELECTORS_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def parse_price(text: str | None) -> float | None:
    """Parse '1.234,56 €', '0,02 €' or '12.50' into a float; None if there is no number."""
    if not text:
        return None
    match = re.search(r"\d[\d.,\s]*", text)
    if not match:
        return None
    number = re.sub(r"\s", "", match.group()).rstrip(".,")
    # The last separator is the decimal mark when 1-2 digits follow it; the rest are thousands.
    decimal = re.search(r"[.,](\d{1,2})$", number)
    if decimal:
        whole = re.sub(r"[.,]", "", number[: decimal.start()])
        number = f"{whole}.{decimal.group(1)}"
    else:
        number = re.sub(r"[.,]", "", number)
    try:
        return float(number)
    except ValueError:
        return None


def parse_product_page(html: str, selectors: dict | None = None) -> CardmarketPrice:
    selectors = selectors or load_selectors()
    if any(marker in html for marker in selectors.get("blocked_markers", [])):
        raise CardmarketBlocked("Cardmarket served a bot-protection page")

    soup = BeautifulSoup(html, "html.parser")
    low_labels = {label.casefold() for label in selectors["low_price_labels"]}
    trend_labels = {label.casefold() for label in selectors["trend_price_labels"]}

    low = trend = None
    for label in soup.select(selectors["label_selector"]):
        value = label.find_next_sibling("dd")
        if value is None:
            continue
        text = label.get_text(strip=True).casefold()
        if text in low_labels and low is None:
            low = parse_price(value.get_text(" ", strip=True))
        elif text in trend_labels and trend is None:
            trend = parse_price(value.get_text(" ", strip=True))

    if low is None and trend is None:
        raise CardmarketParseError("No price found on Cardmarket page; check cardmarket_selectors.json")
    return CardmarketPrice(low=low, trend=trend)


# Link extras that Cardmarket's bot protection blocks outright ("Sorry, you have been blocked"):
# Scryfall's links carry them (referrer=scryfall&utm_source=scryfall...).
_TRACKING = ("referrer", "utm_")


def clean_cardmarket_url(url: str | None) -> str | None:
    """A Cardmarket link without the tracking extras, so the page opens instead of being blocked."""
    if not url or not is_cardmarket_url(url):
        return url
    parsed = urlparse(url)
    query = [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True)
             if not key.lower().startswith(_TRACKING)]
    return urlunparse(parsed._replace(query=urlencode(query)))


def is_cardmarket_url(url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.hostname or ""
    return parsed.scheme == "https" and (host == "cardmarket.com" or host.endswith(".cardmarket.com"))


class CardmarketScraper:
    def __init__(
        self,
        transport: httpx.AsyncBaseTransport | None = None,
        min_interval: float | None = None,
        selectors: dict | None = None,
    ) -> None:
        if min_interval is None:
            min_interval = get_settings().cardmarket_min_interval
        self._limiter = RateLimiter(min_interval, jitter=min_interval * 0.5)
        self._selectors = selectors
        self._blocked_in_a_row = 0
        self._paused_until = 0.0
        self._client = httpx.AsyncClient(
            headers=BROWSER_HEADERS, timeout=20.0, follow_redirects=True, transport=transport
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def fetch_price(self, url: str) -> CardmarketPrice:
        if not is_cardmarket_url(url):
            raise CardmarketError(f"Not a Cardmarket URL: {url}")
        if self.paused:
            raise CardmarketPaused("Cardmarket is refusing requests; not asking again for a while")
        await self._limiter.wait()
        try:
            response = await self._client.get(url)
        except httpx.HTTPError as exc:
            raise CardmarketError(f"Cardmarket request failed: {exc}") from exc
        try:
            if response.status_code in (403, 429, 503):
                raise CardmarketBlocked(f"Cardmarket refused the request (HTTP {response.status_code})")
            if response.status_code >= 400:
                raise CardmarketError(f"Cardmarket returned HTTP {response.status_code}")
            price = parse_product_page(response.text, self._selectors)
        except CardmarketBlocked:
            self._blocked_in_a_row += 1
            if self._blocked_in_a_row >= BLOCKED_LIMIT:
                self._paused_until = time.monotonic() + BLOCKED_PAUSE_SECONDS
                logger.warning(
                    "Cardmarket refused %s requests in a row; skipping it for %s minutes",
                    self._blocked_in_a_row,
                    BLOCKED_PAUSE_SECONDS // 60,
                )
            raise
        self._blocked_in_a_row = 0
        return price

    @property
    def paused(self) -> bool:
        """True while Cardmarket is being left alone after refusing several requests in a row."""
        return time.monotonic() < self._paused_until
