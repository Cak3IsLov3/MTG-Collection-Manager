import datetime as dt

import pytest
from sqlmodel import select

from app.cardmarket import CardmarketBlocked, CardmarketPrice
from app.jobs import check_wishlist, update_collection_prices
from app.models import Alert, DailyTotal, PriceSnapshot
from app.services.collection import add_card_by_name
from app.services.wishlist import add_wish_by_name, wishlist_rows

NOW = dt.datetime(2026, 1, 10, 12, 0, tzinfo=dt.UTC)


class FakeCardmarket:
    def __init__(self, price=None, error=None):
        self.price, self.error, self.urls = price, error, []

    async def fetch_price(self, url):
        self.urls.append(url)
        if self.error:
            raise self.error
        return CardmarketPrice(low=self.price, trend=None)


class FakeNotifier:
    def __init__(self, configured=True, succeeds=True):
        self.configured, self.succeeds, self.sent = configured, succeeds, []

    async def send_price_alert(self, card, price, target, url, source):
        self.sent.append((card.name, price, target, source))
        return self.succeeds


@pytest.fixture
async def vault(session, scryfall):
    """Lim-Dûl's Vault on the wishlist for <= 10.00 (Scryfall says 12.34)."""
    entry = await add_wish_by_name(session, scryfall, "Lim-Dûl's Vault", target_price=10.0)
    session.commit()
    return entry


async def test_alert_when_price_at_or_below_target(session, vault):
    notifier = FakeNotifier()
    alerts = await check_wishlist(session, FakeCardmarket(price=10.0), notifier, now=NOW)

    assert len(alerts) == 1 and alerts[0].price_at_alert == 10.0
    assert notifier.sent == [("Lim-Dûl's Vault", 10.0, 10.0, "cardmarket")]
    snapshot = session.exec(select(PriceSnapshot).where(PriceSnapshot.source == "cardmarket")).one()
    assert snapshot.price_eur == 10.0
    row = wishlist_rows(session)[0]
    assert (row.current_price, row.price_source, row.below_target) == (10.0, "cardmarket", True)


async def test_no_alert_above_target(session, vault):
    notifier = FakeNotifier()
    assert await check_wishlist(session, FakeCardmarket(price=10.01), notifier, now=NOW) == []
    assert notifier.sent == []


async def test_at_most_one_alert_per_24_hours(session, vault):
    notifier, cardmarket = FakeNotifier(), FakeCardmarket(price=8.0)
    await check_wishlist(session, cardmarket, notifier, now=NOW)
    await check_wishlist(session, cardmarket, notifier, now=NOW + dt.timedelta(hours=6))
    await check_wishlist(session, cardmarket, notifier, now=NOW + dt.timedelta(hours=23, minutes=59))
    assert len(notifier.sent) == 1

    await check_wishlist(session, cardmarket, notifier, now=NOW + dt.timedelta(hours=24, minutes=1))
    assert len(notifier.sent) == 2
    assert len(session.exec(select(Alert)).all()) == 2


async def test_falls_back_to_scryfall_when_cardmarket_blocks(session, vault):
    vault.target_price = 15.0  # Scryfall price is 12.34
    session.add(vault)
    session.commit()
    notifier = FakeNotifier()
    alerts = await check_wishlist(session, FakeCardmarket(error=CardmarketBlocked("403")), notifier, now=NOW)
    assert [a.price_at_alert for a in alerts] == [12.34]
    assert notifier.sent[0][3] == "scryfall"


async def test_scraping_disabled_uses_scryfall(session, vault):
    assert await check_wishlist(session, None, FakeNotifier(), now=NOW) == []  # 12.34 > 10.00


async def test_failed_webhook_is_retried_next_run(session, vault):
    cardmarket = FakeCardmarket(price=5.0)
    assert await check_wishlist(session, cardmarket, FakeNotifier(succeeds=False), now=NOW) == []
    assert len(await check_wishlist(session, cardmarket, FakeNotifier(), now=NOW)) == 1


async def test_alert_is_logged_without_webhook(session, vault):
    notifier = FakeNotifier(configured=False)
    assert len(await check_wishlist(session, FakeCardmarket(price=5.0), notifier, now=NOW)) == 1
    assert notifier.sent == []


async def test_unknown_price_is_skipped(session, scryfall):
    await add_wish_by_name(session, scryfall, "Black Lotus", target_price=1000.0)  # Scryfall has no EUR price
    session.commit()
    assert await check_wishlist(session, FakeCardmarket(price=None), FakeNotifier(), now=NOW) == []
    assert wishlist_rows(session)[0].current_price is None


async def test_inactive_entries_are_not_checked(session, vault):
    vault.active = False
    session.add(vault)
    session.commit()
    cardmarket = FakeCardmarket(price=1.0)
    assert await check_wishlist(session, cardmarket, FakeNotifier(), now=NOW) == []
    assert cardmarket.urls == []


async def test_daily_price_update_snapshots_and_totals(session, scryfall, fake_scryfall, vault):
    await add_card_by_name(session, scryfall, "Sol Ring", quantity=2)
    session.commit()

    fake_scryfall.set_price("id-sol", "2.00")
    fake_scryfall.set_price("id-vault", "9.00")
    assert await update_collection_prices(session, scryfall) == 2

    total = session.exec(select(DailyTotal)).one()
    assert (total.collection_value, total.wishlist_cost) == (4.0, 9.0)
    # Same day, same source: the snapshot is overwritten instead of duplicated.
    assert len(session.exec(select(PriceSnapshot)).all()) == 2


async def test_check_fetches_fresh_scryfall_prices_first(session, scryfall, fake_scryfall, vault):
    # Stored price is 12.34 and the target 10.00; Scryfall now says 9.50.
    fake_scryfall.set_price("id-vault", "9.50")
    notifier = FakeNotifier()
    blocked = FakeCardmarket(error=CardmarketBlocked("403"))

    assert await check_wishlist(session, blocked, notifier, now=NOW) == []  # without a refresh: still 12.34
    alerts = await check_wishlist(session, blocked, notifier, now=NOW, scryfall=scryfall)
    assert [a.price_at_alert for a in alerts] == [9.5] and notifier.sent[0][3] == "scryfall"
    assert wishlist_rows(session)[0].current_price == 9.5

    posts = [r for r in fake_scryfall.requests if r.url.path == "/cards/collection"]
    assert len(posts) == 1  # one bulk request, not one per card


async def test_check_survives_a_scryfall_outage(session, vault):
    import httpx

    from app.scryfall import ScryfallClient

    down = ScryfallClient(transport=httpx.MockTransport(lambda r: httpx.Response(500)), min_interval=0)
    vault.target_price = 15.0
    session.add(vault)
    session.commit()
    alerts = await check_wishlist(session, None, FakeNotifier(), now=NOW, scryfall=down)
    assert [a.price_at_alert for a in alerts] == [12.34]  # falls back to the stored price


async def test_paused_cardmarket_is_skipped_quietly(session, vault, caplog):
    from app.cardmarket import CardmarketPaused

    vault.target_price = 15.0
    session.add(vault)
    session.commit()
    paused = FakeCardmarket(error=CardmarketPaused("paused"))
    with caplog.at_level("WARNING"):
        alerts = await check_wishlist(session, paused, FakeNotifier(), now=NOW)
    assert len(alerts) == 1 and "Cardmarket check failed" not in caplog.text
