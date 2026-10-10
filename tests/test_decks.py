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

def test_adding_a_deck_saves_it_unless_only_checking(client):
    client.post("/api/collection", json={"name": "Sol Ring"})
    checked = client.post("/deck", data={"decklist": "1 Sol Ring", "action": "check"})
    assert "This deck is not saved yet." in checked.text
    assert client.get("/api/decks").json() == []

    saved = client.post("/deck", data={"decklist": "1 Sol Ring", "deck_name": "Ramp", "action": "save"})
    assert "Saved Ramp" in saved.text and "This deck is complete." in saved.text
    unnamed = client.post("/deck", files={"file": ("storm.txt", b"1 Sol Ring\n", "text/plain")}, data={"action": "save"})
    assert "Saved storm" in unnamed.text
    assert [d["name"] for d in client.get("/api/decks").json()] == ["Ramp", "storm"]


async def test_choose_which_printing_a_deck_uses(session, scryfall):
    from sqlmodel import select

    from app.models import DeckEntry
    from app.services.decks import choose_printing

    cheap = await add_card_by_name(session, scryfall, "Sol Ring", set_code="cmm")   # 1.50
    pricey = await add_card_by_name(session, scryfall, "Sol Ring", set_code="lea")  # 900.00
    vault = await add_card_by_name(session, scryfall, "Lim-Dûl's Vault")
    first = save_deck(session, "First", [DeckCard("Sol Ring", 1)])
    second = save_deck(session, "Second", [DeckCard("Sol Ring", 1)])
    session.commit()

    def sol(deck):
        return saved_deck_rows(session, deck)[0]

    def used(deck):
        return [(p.card.set_code, n) for p, n in sol(deck).picks]

    # Without a choice the oldest deck takes the cheapest copy.
    assert (used(first), used(second)) == ([("cmm", 1)], [("lea", 1)])
    assert (sol(first).value, sol(second).value) == (1.5, 900.0)
    assert [(s.deck.name, s.value) for s in deck_summaries(session)] == [("First", 1.5), ("Second", 900.0)]

    # A chosen printing sticks, even over an older deck that would otherwise take it.
    entry = session.exec(select(DeckEntry).where(DeckEntry.deck_id == second.id)).one()
    choose_printing(session, second, entry.id, cheap.id)
    assert (used(first), used(second)) == ([("lea", 1)], [("cmm", 1)])
    assert sol(second).pinned_id == cheap.id and sol(second).free == {cheap.id: 1, pricey.id: 0}

    # A printing of another card, or a card of another deck, is refused.
    with pytest.raises(DeckError):
        choose_printing(session, second, entry.id, vault.id)
    with pytest.raises(DeckError):
        choose_printing(session, first, entry.id, cheap.id)

    # Back to automatic; and a chosen printing that leaves the collection is simply dropped.
    choose_printing(session, second, entry.id, None)
    assert used(second) == [("lea", 1)]
    choose_printing(session, second, entry.id, pricey.id)
    session.delete(pricey)
    session.flush()
    assert (used(first), used(second), sol(second).missing) == ([("cmm", 1)], [], 1)


def test_choose_printing_through_pages_and_api(client):
    cheap = client.post("/api/collection", json={"name": "Sol Ring", "set_code": "cmm"}).json()
    pricey = client.post("/api/collection", json={"name": "Sol Ring", "set_code": "lea"}).json()
    deck = client.post("/api/decks", json={"name": "Ramp", "decklist": "1 Sol Ring"}).json()
    card = deck["cards"][0]
    assert (deck["value"], card["using"][0]["printing"]["id"], card["chosen_collection_entry_id"]) == (1.5, cheap["id"], None)

    page = client.get(f"/decks/{deck['id']}")
    assert "Choose for me" in page.text and "worth <strong>€1.50</strong>" in page.text

    form = client.post(f"/decks/{deck['id']}/cards/{card['entry_id']}/printing", data={"collection_entry_id": str(pricey["id"])})
    assert form.status_code == 200 and "worth <strong>€900.00</strong>" in form.text
    assert client.get("/api/decks").json()[0]["value"] == 900.0

    url = f"/api/decks/{deck['id']}/cards/{card['entry_id']}/printing"
    assert client.put(url, json={"collection_entry_id": None}).json()["value"] == 1.5
    assert client.put(url, json={"collection_entry_id": 9999}).status_code == 400
    assert client.put(f"/api/decks/9999/cards/{card['entry_id']}/printing", json={}).status_code == 404
