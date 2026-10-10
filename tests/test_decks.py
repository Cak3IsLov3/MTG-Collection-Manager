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


def test_basic_lands_and_tokens_are_left_out_by_default():
    from app.services.decks import archidekt_bracket, parse_archidekt_deck, select_tracked

    text = """1 Sol Ring
10 Forest
3 Snow-Covered Island
2 forest
1 Command Tower
2 Treasure Token
1x Rabbit (tblb) 21 [Tokens & Extras{noDeck}{noPrice}]
1x Hare Apparent (fdn) 15 [Tokens]
1x Mana Crypt (2xm) 270 [Maybeboard{noDeck}{noPrice},Ramp]
"""
    cards = parse_decklist(text)
    assert "Mana Crypt" not in [c.name for c in cards]  # maybeboard is not part of the deck at all

    tracked, left_out = select_tracked(cards)
    # Non-basic lands stay, and a user category called "Tokens" is a theme, not tokens.
    assert [c.name for c in tracked] == ["Sol Ring", "Command Tower", "Hare Apparent"]
    assert left_out.basics == {"Forest": 12, "Snow-Covered Island": 3}  # per land, whatever the printing
    assert (left_out.basic_total, left_out.tokens) == (15, 3)

    tracked, left_out = select_tracked(cards, include_basics=True)
    assert "Forest" in [c.name for c in tracked] and left_out.basics == {} and left_out.tokens == 3
    assert select_tracked(cards, include_basics=True, include_tokens=True)[1].tokens == 0

    archidekt = {
        "name": "Rabbits",
        "edhBracket": 3,
        "categories": [
            {"name": "Land", "includedInDeck": True},
            {"name": "Tokens", "includedInDeck": True},
            {"name": "Tokens & Extras", "includedInDeck": False},
            {"name": "Sideboard", "includedInDeck": False},
        ],
        "cards": [
            {"quantity": 5, "categories": ["Land"], "card": {"oracleCard": {"name": "Plains", "superTypes": ["Basic"], "types": ["Land"]}}},
            {"quantity": 2, "categories": ["Land"], "card": {"oracleCard": {"name": "Plains", "superTypes": ["Basic"], "types": ["Land"]}}},
            {"quantity": 1, "categories": ["Land"], "card": {"oracleCard": {"name": "Command Tower", "superTypes": [], "types": ["Land"]}}},
            {"quantity": 17, "categories": ["Tokens"], "card": {"oracleCard": {"name": "Hare Apparent", "types": ["Creature"]}}},
            {"quantity": 4, "categories": ["Tokens & Extras"], "card": {"oracleCard": {"name": "Rabbit", "layout": "token", "types": ["Token", "Creature"]}}},
            {"quantity": 1, "categories": ["Sideboard"], "card": {"oracleCard": {"name": "Sol Ring", "types": ["Artifact"]}}},
        ],
    }
    _, cards = parse_archidekt_deck(archidekt)
    tracked, left_out = select_tracked(cards)
    assert [(c.quantity, c.name) for c in tracked] == [(1, "Command Tower"), (17, "Hare Apparent")]
    assert (left_out.basics, left_out.tokens) == ({"Plains": 7}, 4)
    assert archidekt_bracket(archidekt) == 3 and archidekt_bracket({"edhBracket": None}) is None


def test_saved_deck_keeps_its_basic_lands_per_land(client):
    client.post("/api/collection", json={"name": "Sol Ring"})
    decklist = "1 Sol Ring\n20 Forest\n10 Plains\n2 Treasure Token"

    checked = client.post("/deck", data={"decklist": decklist, "action": "check"})
    assert "You have 1 of the 1 cards for this deck available (100%)" in checked.text
    assert "30 basic lands (10 Plains, 20 Forest) and 2 tokens" in checked.text
    assert 'name="basic:Forest" value="20"' in checked.text

    saved = client.post("/deck", data={"decklist": decklist, "deck_name": "Ramp", "action": "save"})
    assert "This deck is complete." in saved.text and "30 basic lands and 2 tokens" in saved.text
    assert "31 cards in total" in saved.text  # tokens are extras, not part of the deck size
    deck = client.get("/api/decks").json()[0]
    assert (deck["cards_needed"], deck["complete"], deck["basic_land_count"]) == (1, True, 30)
    detail = client.get(f"/api/decks/{deck['id']}").json()
    assert detail["basic_lands"] == {"Plains": 10, "Forest": 20} and detail["untracked"] == {"basic": 30, "token": 2}

    included = client.post("/api/decks", json={"name": "All", "decklist": decklist, "include_basic_lands": True}).json()
    assert (included["cards_needed"], included["basic_lands"], included["untracked"]) == (31, {}, {"token": 2})

    # Saving after "Only check" keeps the lands and the bracket; deleting a deck cleans up.
    again = client.post("/deck/save", data={"name": "Later", "decklist": "1 Sol Ring", "basic:Island": "7", "skipped_token": "0", "bracket": "4"})
    assert "7 basic lands." in again.text
    later = client.get("/api/decks").json()[-1]
    assert (later["basic_land_count"], later["bracket"], later["bracket_name"]) == (7, 4, "Optimized")
    for d in client.get("/api/decks").json():
        assert client.delete(f"/api/decks/{d['id']}").status_code == 204

    only_lands = client.post("/api/decks/check", json={"decklist": "20 Island"})
    assert only_lands.status_code == 400 and "basic lands and tokens" in only_lands.json()["detail"]


def test_edit_basic_lands_of_a_saved_deck(client):
    # A deck that was saved without any basic lands, like the ones imported before this existed.
    deck = client.post("/api/decks", json={"name": "Old", "decklist": "1 Sol Ring"}).json()
    assert deck["basic_lands"] == {} and deck["basic_land_count"] == 0
    url = f"/api/decks/{deck['id']}/basic-lands"

    updated = client.put(url, json={"Plains": 10, "forest": 3, "Island": 0}).json()
    assert updated["basic_lands"] == {"Plains": 10, "Forest": 3}  # zero is dropped, names are normalised
    assert (updated["basic_land_count"], updated["cards_needed"]) == (13, 1)  # lands are not tracked cards

    page = client.get(f"/decks/{deck['id']}")
    assert 'name="basic:Plains" type="number" min="0" step="1" value="10"' in page.text
    assert 'name="basic:Swamp" type="number" min="0" step="1" value="0"' in page.text
    assert "14 cards in total" in page.text

    form = client.post(f"/decks/{deck['id']}/basics", data={"basic:Plains": "8", "basic:Snow-Covered Forest": "2", "basic:Forest": ""})
    assert "Basic lands updated" in form.text
    assert client.get(f"/api/decks/{deck['id']}").json()["basic_lands"] == {"Plains": 8, "Snow-Covered Forest": 2}

    assert client.put(url, json={"Sol Ring": 1}).status_code == 400
    assert client.put(url, json={"Plains": -1}).status_code == 400
    assert "not a number" in client.post(f"/decks/{deck['id']}/basics", data={"basic:Plains": "many"}).text
    assert client.put("/api/decks/9999/basic-lands", json={}).status_code == 404
    assert client.get(f"/api/decks/{deck['id']}").json()["basic_lands"] == {"Plains": 8, "Snow-Covered Forest": 2}


def test_deck_bracket(client):
    deck = client.post("/api/decks", json={"name": "Casual", "decklist": "1 Sol Ring", "bracket": 2}).json()
    assert (deck["bracket"], deck["bracket_name"]) == (2, "Core")
    url = f"/api/decks/{deck['id']}/bracket"

    assert client.put(url, json={"bracket": 4}).json()["bracket_name"] == "Optimized"
    assert client.put(url, json={"bracket": 6}).status_code == 422
    listing = client.get("/deck")
    assert "4 · Optimized" in listing.text

    page = client.post(f"/decks/{deck['id']}/bracket", data={"bracket": "5"})
    assert '<option value="5" selected>5 · cEDH</option>' in page.text
    assert "1 to 5" in client.post(f"/decks/{deck['id']}/bracket", data={"bracket": "9"}).text
    client.post(f"/decks/{deck['id']}/bracket", data={"bracket": ""})
    cleared = client.get(f"/api/decks/{deck['id']}").json()
    assert (cleared["bracket"], cleared["bracket_name"]) == (None, None)
    assert client.put("/api/decks/9999/bracket", json={"bracket": 1}).status_code == 404


async def test_archidekt_deck_brings_its_bracket_and_lands(session, monkeypatch):
    from app.routers.api import load_deck
    from app.services import decks as deck_service

    async def fake_json(deck_id, transport=None):
        return {
            "name": "Rabbit Tribal",
            "edhBracket": 3,
            "cards": [
                {"quantity": 10, "categories": ["Land"], "card": {"oracleCard": {"name": "Plains", "superTypes": ["Basic"], "types": ["Land"]}}},
                {"quantity": 1, "categories": ["Ramp"], "card": {"oracleCard": {"name": "Sol Ring", "types": ["Artifact"]}}},
            ],
        }

    monkeypatch.setattr(deck_service, "fetch_archidekt_json", fake_json)
    loaded = await load_deck(None, "https://archidekt.com/decks/18350424/")
    assert (loaded.name, loaded.bracket, loaded.left_out.basics) == ("Rabbit Tribal", 3, {"Plains": 10})
    deck = deck_service.save_deck(session, loaded.name, loaded.cards, loaded.left_out, loaded.bracket)
    assert deck.bracket == 3 and deck_service.deck_left_out(session, deck).basics == {"Plains": 10}


def test_deck_page_has_a_search_box(client):
    deck = client.post("/api/decks", json={"name": "Ramp", "decklist": "1 Sol Ring\n1 Fire // Ice"}).json()
    page = client.get(f"/decks/{deck['id']}")
    assert 'id="deck-search"' in page.text and page.text.count("data-deck-row=") == 2
    assert 'id="deck-search"' in client.post("/deck", data={"decklist": "1 Sol Ring"}).text
    assert 'id="deck-search"' not in client.get("/deck").text  # nothing to search yet


def test_bracket_column_is_added_to_an_existing_database():
    from sqlalchemy import create_engine

    from app.database import add_missing_columns

    old = create_engine("sqlite://")
    with old.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE deck (id INTEGER PRIMARY KEY, name VARCHAR NOT NULL, created_at DATETIME)")
        connection.exec_driver_sql("INSERT INTO deck (name) VALUES ('Marwyn')")
    add_missing_columns(old)
    add_missing_columns(old)  # running it again changes nothing
    with old.begin() as connection:
        assert connection.exec_driver_sql("SELECT name, bracket FROM deck").all() == [("Marwyn", None)]


def test_move_basic_lands_out_of_an_older_deck(client):
    # Older decks were saved with their basic lands as ordinary cards.
    client.post("/api/collection", json={"name": "Sol Ring"})
    deck = client.post("/api/decks", json={"name": "Old", "decklist": "1 Sol Ring\n30 Forest\n4 Island", "include_basic_lands": True}).json()
    assert (deck["cards_needed"], deck["cards_usable"], deck["basic_lands"]) == (35, 1, {})
    client.put(f"/api/decks/{deck['id']}/basic-lands", json={"Forest": 1})

    page = client.get(f"/decks/{deck['id']}")
    assert "still has 34 basic lands in its card list" in page.text

    moved = client.post(f"/decks/{deck['id']}/basics/move")
    assert "Moved 34 basic lands out of the card list" in moved.text and "still has" not in moved.text
    after = client.get(f"/api/decks/{deck['id']}").json()
    assert (after["cards_needed"], after["complete"]) == (1, True)
    assert after["basic_lands"] == {"Island": 4, "Forest": 31}  # added to what was already filled in
    assert [c["name"] for c in after["cards"]] == ["Sol Ring"]

    again = client.post(f"/api/decks/{deck['id']}/basic-lands/move").json()  # nothing left to move
    assert again["basic_lands"] == {"Island": 4, "Forest": 31}
