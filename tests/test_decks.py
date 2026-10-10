import httpx
import pytest

from app.services.collection import add_card_by_name
from app.services.decks import (
    DeckCard,
    DeckError,
    add_missing_to_wishlist,
    archidekt_deck_id,
    check_deck,
    fetch_archidekt_deck,
    parse_deck_file,
    parse_decklist,
    save_deck,
    saved_deck_rows,
    deck_summaries,
    delete_deck,
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
    # "Fire" is the same physical card as "Fire // Ice", so the two lines are one row.
    assert "Fire" not in rows
    assert (rows["Fire // Ice"].needed, rows["Fire // Ice"].owned, rows["Fire // Ice"].missing) == (3, 1, 2)
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
    assert "You have 1 of the 5 cards for this deck available (20%)" in page.text
    assert "Available in your collection (1)" in page.text and "Set LEA (LEA #1)" in page.text
    assert "Still missing (1)" in page.text

    uploaded = client.post("/deck", files={"file": ("deck.txt", b"1 Sol Ring\n", "text/plain")})
    assert "You have every card for this deck available." in uploaded.text
    assert 'value="deck"' in uploaded.text  # deck name suggested from the file name

    api = client.post("/api/decks/check", json={"decklist": "1 Sol Ring"}).json()
    assert api[0]["printings"][0]["card"]["set_code"] == "lea"

async def test_saved_decks_do_not_share_the_same_copy(session, scryfall):
    await add_card_by_name(session, scryfall, "Sol Ring", quantity=1)
    await add_card_by_name(session, scryfall, "Fire // Ice", quantity=3)
    first = save_deck(session, "First", [DeckCard("Sol Ring", 1), DeckCard("Fire", 2)])
    second = save_deck(session, "Second", [DeckCard("Sol Ring", 1), DeckCard("Fire // Ice", 2)])
    session.commit()

    # The oldest deck gets the only Sol Ring; the second deck is told who has it.
    sol = {row.name: row for row in saved_deck_rows(session, first)}["Sol Ring"]
    assert (sol.usable, sol.missing, sol.used_by) == (1, 0, [])
    rows = {row.name: row for row in saved_deck_rows(session, second)}
    assert (rows["Sol Ring"].owned, rows["Sol Ring"].available, rows["Sol Ring"].missing) == (1, 0, 1)
    assert rows["Sol Ring"].used_by == ["First"]
    assert (rows["Fire // Ice"].reserved, rows["Fire // Ice"].usable, rows["Fire // Ice"].missing) == (2, 1, 1)
    assert [(s.deck.name, s.usable, s.needed, s.complete) for s in deck_summaries(session)] == [
        ("First", 3, 3, True), ("Second", 1, 3, False),
    ]

    # A deck you are only checking gets what every saved deck leaves over.
    checked = {row.name: row for row in check_deck(session, [DeckCard("Sol Ring", 1), DeckCard("Fire // Ice", 1)])}
    assert checked["Sol Ring"].available == 0 and checked["Sol Ring"].used_by == ["First"]
    assert checked["Fire // Ice"].available == 0 and checked["Fire // Ice"].used_by == ["First", "Second"]

    # A second copy goes to the next deck in line; deleting a deck frees its cards.
    await add_card_by_name(session, scryfall, "Sol Ring", quantity=1)
    assert {row.name: row for row in saved_deck_rows(session, second)}["Sol Ring"].missing == 0
    delete_deck(session, first)
    session.commit()
    assert [(s.deck.name, s.complete) for s in deck_summaries(session)] == [("Second", True)]


def test_save_deck_through_pages_and_api(client):
    client.post("/api/collection", json={"name": "Sol Ring"})
    saved = client.post("/deck/save", data={"name": "Artifacts", "decklist": "1 Sol Ring\n1 Fire // Ice"})
    assert saved.status_code == 200 and "Saved Artifacts" in saved.text
    assert "1 of the 2 cards are set aside for this deck (50%)" in saved.text

    checked = client.post("/deck", data={"decklist": "1 Sol Ring"})
    assert "all copies in other decks" in checked.text and "1 in use by Artifacts" in checked.text
    assert "Your saved decks" in client.get("/deck").text

    created = client.post("/api/decks", json={"name": "Second", "decklist": "1 Sol Ring"})
    assert created.status_code == 201
    body = created.json()
    assert (body["cards_usable"], body["complete"], body["cards"][0]["used_by"]) == (0, False, ["Artifacts"])
    assert [d["name"] for d in client.get("/api/decks").json()] == ["Artifacts", "Second"]

    first_id = client.get("/api/decks").json()[0]["id"]
    assert client.delete(f"/api/decks/{first_id}").status_code == 204
    assert client.get(f"/api/decks/{body['id']}").json()["complete"] is True
    assert client.get("/api/decks/9999").status_code == 404