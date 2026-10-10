import httpx
import pytest

from app.services.collection import add_card_by_name
from app.services.decks import (
    DeckError,
    add_missing_to_wishlist,
    archidekt_deck_id,
    check_deck,
    fetch_archidekt_deck,
    parse_deck_file,
    parse_decklist,
)
from app.services.wishlist import wishlist_rows

DECKLIST = """
// Commander
1x Sol Ring (cmm) 400 [Ramp]
2 Fire // Ice
1 Lim-Dûl's Vault *F* ^Have,#37d67a^
Sideboard
# a comment
1 Sol Ring
"""


def test_parse_decklist():
    cards = parse_decklist(DECKLIST)
    assert [(c.quantity, c.name) for c in cards] == [
        (1, "Sol Ring"), (2, "Fire // Ice"), (1, "Lim-Dûl's Vault"), (1, "Sol Ring"),
    ]


def test_archidekt_deck_id():
    assert archidekt_deck_id("https://archidekt.com/decks/123456/my-deck") == 123456
    assert archidekt_deck_id("https://www.archidekt.com/api/decks/42/") == 42
    assert archidekt_deck_id("https://moxfield.com/decks/abc") is None


async def test_fetch_archidekt_deck():
    payload = {
        "name": "Test deck",
        "cards": [
            {"quantity": 1, "categories": ["Ramp"], "card": {"oracleCard": {"name": "Sol Ring"}}},
            {"quantity": 2, "categories": ["Maybeboard"], "card": {"oracleCard": {"name": "Black Lotus"}}},
            {"quantity": 1, "categories": None, "card": {}},
        ],
    }
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json=payload))
    name, cards = await fetch_archidekt_deck(1, transport=transport)
    assert name == "Test deck"
    assert [(c.quantity, c.name) for c in cards] == [(1, "Sol Ring")]

    with pytest.raises(DeckError):
        await fetch_archidekt_deck(1, transport=httpx.MockTransport(lambda r: httpx.Response(404)))


async def test_check_deck_and_wishlist_missing(session, scryfall):
    await add_card_by_name(session, scryfall, "Sol Ring", quantity=1)
    await add_card_by_name(session, scryfall, "Fire // Ice", quantity=1)
    session.commit()

    rows = {row.name: row for row in check_deck(session, parse_decklist(DECKLIST + "1 Fire\n"))}
    assert (rows["Sol Ring"].needed, rows["Sol Ring"].owned, rows["Sol Ring"].missing) == (2, 1, 1)
    assert rows["Fire // Ice"].missing == 1
    assert rows["Fire"].owned == 1 and rows["Fire"].missing == 0  # owned as "Fire // Ice"
    # The owned printing is listed, so you know which copy to pull.
    assert [(p.card.set_code, p.entry.quantity) for p in rows["Sol Ring"].printings] == [("cmm", 1)]
    assert rows["Sol Ring"].usable == 1 and rows["Lim-Dûl's Vault"].printings == []
    assert rows["Lim-Dûl's Vault"].missing == 1 and not rows["Lim-Dûl's Vault"].on_wishlist

    added, not_found = await add_missing_to_wishlist(session, scryfall, ["Lim-Dûl's Vault", "Nope"], target_pct=90)
    session.commit()
    assert (added, not_found) == (1, ["Nope"])
    assert wishlist_rows(session)[0].entry.target_price == 11.11  # 90% of 12.34

    rows = {row.name: row for row in check_deck(session, parse_decklist(DECKLIST))}
    assert rows["Lim-Dûl's Vault"].on_wishlist


def test_parse_deck_file_accepts_text_and_csv():
    assert [(c.quantity, c.name) for c in parse_deck_file(b"2 Sol Ring\n1 Fire // Ice\n")] == [(2, "Sol Ring"), (1, "Fire // Ice")]
    assert [(c.quantity, c.name) for c in parse_deck_file("Quantity,Name\n3,Lim-Dûl's Vault\n".encode())] == [(3, "Lim-Dûl's Vault")]
    assert parse_deck_file(b"") == []


def test_deck_page_shows_owned_cards_with_their_printing(client):
    client.post("/api/collection", json={"name": "Sol Ring", "set_code": "lea", "quantity": 1})
    page = client.post("/deck", data={"decklist": "1 Sol Ring\n4 Fire // Ice"})
    assert page.status_code == 200
    assert "You own 1 of the 5 cards in this deck (20%)" in page.text
    assert "In your collection (1)" in page.text and "Set LEA (LEA #1)" in page.text
    assert "Still missing (1)" in page.text

    uploaded = client.post("/deck", files={"file": ("deck.txt", b"1 Sol Ring\n", "text/plain")})
    assert "You own every card in this deck." in uploaded.text

    api = client.post("/api/decks/check", json={"decklist": "1 Sol Ring"}).json()
    assert api[0]["printings"][0]["card"]["set_code"] == "lea"