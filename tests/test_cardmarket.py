import httpx
import pytest

from app.cardmarket import (
    CardmarketBlocked,
    CardmarketError,
    CardmarketParseError,
    CardmarketScraper,
    is_cardmarket_url,
    parse_price,
    parse_product_page,
)

URL = "https://www.cardmarket.com/en/Magic/Products/Singles/Alliances/Lim-Duls-Vault"

PRODUCT_PAGE = """
<html><head><title>Lim-Dûl's Vault</title></head><body>
<div class="info-list-container">
  <dl class="labeled row mx-auto g-0">
    <dt class="col-6">Rarity</dt><dd class="col-6">Uncommon</dd>
    <dt class="col-6">Available items</dt><dd class="col-6">1234</dd>
    <dt class="col-6">From</dt><dd class="col-6">11,95 €</dd>
    <dt class="col-6">Price Trend</dt><dd class="col-6"><span>1.234,56 €</span></dd>
  </dl>
</div></body></html>
"""


@pytest.mark.parametrize(
    "text, expected",
    [
        ("11,95 €", 11.95),
        ("0,02 €", 0.02),
        ("1.234,56 €", 1234.56),
        ("€ 12.50", 12.5),
        ("1,234.56", 1234.56),
        ("5 €", 5.0),
        ("1.234", 1234.0),
        ("N/A", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_price(text, expected):
    assert parse_price(text) == expected


def test_parse_product_page():
    price = parse_product_page(PRODUCT_PAGE)
    assert price.low == 11.95
    assert price.trend == 1234.56


def test_selectors_come_from_config():
    html = '<table><tr><td class="k">Cheapest</td></tr></table><dl><dt>Cheapest</dt><dd>3,00 €</dd></dl>'
    selectors = {"label_selector": "dl dt", "low_price_labels": ["Cheapest"], "trend_price_labels": []}
    assert parse_product_page(html, selectors).low == 3.0


def test_page_without_price_raises_parse_error():
    with pytest.raises(CardmarketParseError):
        parse_product_page("<html><body><h1>Layout changed</h1></body></html>")


def test_bot_protection_page_is_reported_as_blocked():
    with pytest.raises(CardmarketBlocked):
        parse_product_page("<html><head><title>Just a moment...</title></head></html>")


def test_only_cardmarket_urls_are_allowed():
    assert is_cardmarket_url(URL)
    assert not is_cardmarket_url("https://evil.example/cardmarket.com")
    assert not is_cardmarket_url("http://www.cardmarket.com/en/Magic")
    assert not is_cardmarket_url("https://cardmarket.com.evil.example/")


def _scraper(handler) -> CardmarketScraper:
    return CardmarketScraper(transport=httpx.MockTransport(handler), min_interval=0)


async def test_fetch_price():
    price = await _scraper(lambda r: httpx.Response(200, text=PRODUCT_PAGE)).fetch_price(URL)
    assert price.low == 11.95


@pytest.mark.parametrize("status", [403, 429, 503])
async def test_fetch_price_blocked(status):
    with pytest.raises(CardmarketBlocked):
        await _scraper(lambda r: httpx.Response(status)).fetch_price(URL)


async def test_fetch_price_network_error():
    def handler(request):
        raise httpx.ConnectError("boom")

    with pytest.raises(CardmarketError):
        await _scraper(handler).fetch_price(URL)


async def test_fetch_price_rejects_other_hosts():
    with pytest.raises(CardmarketError):
        await _scraper(lambda r: httpx.Response(200, text=PRODUCT_PAGE)).fetch_price("https://example.com/")
