"""A printing without a price borrows the price of another printing of the same card."""

import httpx
from sqlmodel import select

from app.jobs import check_wishlist, update_collection_prices
from app.models import PriceSnapshot
from app.scryfall import ScryfallCard, ScryfallClient
from app.services.collection import add_card_by_name, collection_rows
from app.services.wishlist import add_wish_by_name, wishlist_rows
from tests.conftest import scryfall_json
from tests.test_alerts import NOW, FakeNotifier


def other_printings(fake_scryfall):
    """Black Lotus (LEA) has no price in the fake data; give it two priced printings."""
    fake_scryfall.cards += [
        scryfall_json("Black Lotus", "id-lotus-leb", "leb", eur="9000.00"),
        scryfall_json("Black Lotus", "id-lotus-2ed", "2ed", eur="7500.00", eur_foil="20000.00"),
    ]


def test_card_without_a_price_gets_the_cheapest_other_printing(client, fake_scryfall):
    other_printings(fake_scryfall)
    added = client.post("/api/collection", json={"name": "Black Lotus", "set_code": "lea", "quantity": 2}).json()
    assert added["card"]["set_code"] == "lea"  # the card itself stays the printing you own
    assert (added["unit_price"], added["price_from_set"], added["total_price"]) == (7500.0, "2ed", 15000.0)
    assert client.get("/api/collection").json()["total_value"] == 15000.0

    page = client.get("/collection")
    assert "≈ 2ED" in page.text and "cheapest printing with a price" in page.text

    # A printing with its own price is left alone.
    sol = client.post("/api/collection", json={"name": "Sol Ring", "set_code": "cmm"}).json()
    assert (sol["unit_price"], sol["price_from_set"]) == (1.5, None)


def test_no_other_printing_with_a_price_stays_unpriced(client):
    added = client.post("/api/collection", json={"name": "Black Lotus"}).json()  # only the unpriced LEA exists
    assert (added["unit_price"], added["price_from_set"]) == (None, None)


def test_foil_price_is_only_borrowed_for_foil_copies(client, fake_scryfall):
    fake_scryfall.cards.append(scryfall_json("Lim-Dûl's Vault", "id-vault-foil", "c13", eur="3.00", eur_foil="8.00"))
    regular = client.post("/api/collection", json={"name": "Lim-Dûl's Vault", "set_code": "all"}).json()
    assert (regular["unit_price"], regular["price_from_set"]) == (12.34, None)  # own price, no lookup needed

    foil = client.post("/api/collection", json={"name": "Lim-Dûl's Vault", "set_code": "all", "is_foil": True}).json()
    assert (foil["unit_price"], foil["price_from_set"]) == (8.0, "c13")
    searches = [r for r in fake_scryfall.requests if r.url.path == "/cards/search"]
    assert len(searches) == 1  # only for the foil copy


async def test_daily_update_fills_and_later_drops_the_borrowed_price(session, scryfall, fake_scryfall):
    await add_card_by_name(session, scryfall, "Black Lotus")
    session.commit()
    assert collection_rows(session)[0].unit_price is None

    other_printings(fake_scryfall)  # another printing gets a price
    await update_collection_prices(session, scryfall)
    row = collection_rows(session)[0]
    assert (row.unit_price, row.price_from_set) == (7500.0, "2ed")

    fake_scryfall.set_price("id-lotus", "12000.00")  # the printing itself gets a price
    await update_collection_prices(session, scryfall)
    row = collection_rows(session)[0]
    assert (row.unit_price, row.price_from_set) == (12000.0, None)
    assert len(session.exec(select(PriceSnapshot)).all()) == 1  # still one snapshot per day


async def test_wishlist_uses_a_borrowed_price_for_alerts(session, scryfall, fake_scryfall):
    await add_wish_by_name(session, scryfall, "Black Lotus", target_price=8000.0)
    session.commit()
    assert wishlist_rows(session)[0].current_price is None
    assert await check_wishlist(session, None, FakeNotifier(), now=NOW, scryfall=scryfall) == []

    other_printings(fake_scryfall)
    alerts = await check_wishlist(session, None, FakeNotifier(), now=NOW, scryfall=scryfall)
    row = wishlist_rows(session)[0]
    assert (row.current_price, row.price_from_set, row.below_target) == (7500.0, "2ed", True)
    assert [a.price_at_alert for a in alerts] == [7500.0]


def test_wishlist_page_and_api_show_where_the_price_comes_from(client, fake_scryfall):
    other_printings(fake_scryfall)
    added = client.post("/api/wishlist", json={"name": "Black Lotus", "target_price": 100}).json()
    assert (added["current_price"], added["price_from_set"]) == (7500.0, "2ed")
    assert "≈ 2ED" in client.get("/wishlist").text


async def test_names_are_looked_up_in_batches(fake_scryfall):
    client = ScryfallClient(transport=httpx.MockTransport(fake_scryfall), min_interval=0)
    cards = [ScryfallCard.from_json(scryfall_json(f"Card {i}", f"id-{i}", eur=None)) for i in range(20)]
    cards.append(ScryfallCard.from_json(scryfall_json('Kongming, "Sleeping Dragon"', "id-quote", eur=None)))
    assert await client.fill_missing_prices(cards) == 0
    assert len(fake_scryfall.requests) == 3  # 20 names, 8 per query; a name with quotes is skipped


async def test_a_failing_lookup_does_not_break_adding_a_card(session):
    def handler(request):
        if request.url.path == "/cards/search":
            return httpx.Response(500)
        return httpx.Response(200, json=scryfall_json("Black Lotus", "id-lotus", "lea", eur=None))

    client = ScryfallClient(transport=httpx.MockTransport(handler), min_interval=0)
    entry = await add_card_by_name(session, client, "Black Lotus")
    assert entry.quantity == 1 and collection_rows(session)[0].unit_price is None


def test_columns_are_added_to_an_existing_database():
    from sqlalchemy import create_engine

    from app.database import add_missing_columns

    old = create_engine("sqlite://")
    with old.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE pricesnapshot (id INTEGER PRIMARY KEY, card_id INTEGER, date DATE, source VARCHAR, "
            "price_eur FLOAT, price_eur_foil FLOAT)"
        )
        connection.exec_driver_sql("INSERT INTO pricesnapshot (card_id, source, price_eur) VALUES (1, 'scryfall', 1.5)")
    add_missing_columns(old)
    add_missing_columns(old)
    with old.begin() as connection:
        rows = connection.exec_driver_sql("SELECT price_eur, eur_from_set, eur_foil_from_set FROM pricesnapshot").all()
    assert rows == [(1.5, None, None)]
