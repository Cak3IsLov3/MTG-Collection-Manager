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
from tests.conftest import scryfall_json

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
    assert "You have 31 of the 31 cards for this deck available (100%)" in checked.text  # 1 card + 30 lands
    assert "includes 30 basic lands (10 Plains, 20 Forest), counted as owned" in checked.text
    assert "2 tokens not counted" in checked.text and "69 short of 100 for Commander" in checked.text
    assert 'name="basic:Forest" value="20"' in checked.text

    saved = client.post("/deck", data={"decklist": decklist, "deck_name": "Ramp", "action": "save"})
    assert "This deck is complete." in saved.text and "includes 30 basic lands, counted as owned" in saved.text
    assert "31 of the 31 cards are set aside for this deck (100%)" in saved.text
    assert "Deck size: <strong>31 cards</strong>" in saved.text  # tokens are extras, not part of the deck size
    deck = client.get("/api/decks").json()[0]
    assert (deck["cards_needed"], deck["complete"], deck["basic_land_count"]) == (1, True, 30)
    assert (deck["deck_size"], deck["deck_owned"]) == (31, 31)
    detail = client.get(f"/api/decks/{deck['id']}").json()
    assert detail["basic_lands"] == {"Plains": 10, "Forest": 20} and detail["untracked"] == {"basic": 30, "token": 2}

    included = client.post("/api/decks", json={"name": "All", "decklist": decklist, "include_basic_lands": True}).json()
    assert (included["cards_needed"], included["basic_lands"], included["untracked"]) == (31, {}, {"token": 2})

    # Saving after "Only check" keeps the lands and the bracket; deleting a deck cleans up.
    again = client.post("/deck/save", data={"name": "Later", "decklist": "1 Sol Ring", "basic:Island": "7", "skipped_token": "0", "bracket": "4"})
    assert "includes 7 basic lands, counted as owned" in again.text
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
    assert "Deck size: <strong>14 cards</strong>" in page.text

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


def test_save_button_stores_all_deck_changes_at_once(client):
    cheap = client.post("/api/collection", json={"name": "Sol Ring", "set_code": "cmm"}).json()
    pricey = client.post("/api/collection", json={"name": "Sol Ring", "set_code": "lea"}).json()
    deck = client.post("/api/decks", json={"name": "Ramp", "decklist": "1 Sol Ring"}).json()
    entry_id = deck["cards"][0]["entry_id"]
    url = f"/decks/{deck['id']}/save"

    page = client.get(f"/decks/{deck['id']}")
    assert 'id="deck-form"' in page.text and "Save changes" in page.text and "Save lands" not in page.text
    assert f'name="pick:{entry_id}" form="deck-form"' in page.text
    assert "onchange" not in page.text  # nothing is saved until the button is pressed

    saved = client.post(url, data={"bracket": "3", "basic:Plains": "10", "basic:Forest": "", f"pick:{entry_id}": str(pricey["id"])})
    assert "Changes saved" in saved.text
    detail = client.get(f"/api/decks/{deck['id']}").json()
    assert (detail["bracket"], detail["basic_lands"], detail["value"]) == (3, {"Plains": 10}, 900.0)
    assert detail["cards"][0]["chosen_collection_entry_id"] == pricey["id"]

    # Fields that are cleared go back to "not set" / automatic.
    client.post(url, data={"bracket": "", "basic:Plains": "0", f"pick:{entry_id}": ""})
    detail = client.get(f"/api/decks/{deck['id']}").json()
    assert (detail["bracket"], detail["basic_lands"], detail["cards"][0]["chosen_collection_entry_id"]) == (None, {}, None)

    # One invalid field and nothing at all is stored.
    failed = client.post(url, data={"bracket": "4", "basic:Plains": "-2", f"pick:{entry_id}": str(cheap["id"])})
    assert "Nothing was saved" in failed.text
    detail = client.get(f"/api/decks/{deck['id']}").json()
    assert (detail["bracket"], detail["basic_lands"]) == (None, {})
    assert "Nothing was saved" in client.post(url, data={"bracket": "9"}).text
    assert "Deck not found" in client.post("/decks/9999/save", data={}).text


def test_random_decks():
    import random

    from app.models import Deck
    from app.services.decks import DeckSummary, random_decks

    def summary(deck_id, bracket, usable):
        return DeckSummary(Deck(id=deck_id, name=f"Deck {deck_id}", bracket=bracket), needed=10, usable=usable)

    decks = [summary(1, 2, 10), summary(2, 3, 10), summary(3, 3, 4), summary(4, None, 10)]
    rng = random.Random(1)

    assert {random_decks(decks, rng=rng)[0].deck.id for _ in range(60)} == {1, 2, 3, 4}  # every deck gets a turn
    pod = random_decks(decks, count=4, rng=rng)
    assert sorted(s.deck.id for s in pod) == [1, 2, 3, 4]  # one each, never the same deck twice
    assert {random_decks(decks, bracket=3, rng=rng)[0].deck.id for _ in range(30)} == {2, 3}
    assert {random_decks(decks, bracket=3, complete_only=True, rng=rng)[0].deck.id for _ in range(10)} == {2}

    for kwargs in ({"count": 5}, {"bracket": 5}, {"bracket": 3, "complete_only": True, "count": 2}, {"count": 0}):
        with pytest.raises(DeckError):
            random_decks(decks, rng=rng, **kwargs)


def test_random_deck_page_and_api(client):
    assert "random-go" not in client.get("/deck").text  # nothing to pick from yet
    assert client.get("/api/decks/random").status_code == 400

    client.post("/api/collection", json={"name": "Sol Ring"})
    first = client.post("/api/decks", json={"name": "Ramp", "decklist": "1 Sol Ring", "bracket": 2}).json()
    client.post("/api/decks", json={"name": "Burn & <Friends>", "decklist": "4 Fire // Ice", "bracket": 4})

    page = client.get("/deck")
    assert 'id="random-go"' in page.text and '"name": "Ramp"' in page.text
    assert '<option value="2">2 · Core</option>' in page.text and '<option value="3">' not in page.text
    assert "<Friends>" not in page.text  # deck names are escaped in the embedded data

    picked = client.get("/api/decks/random").json()
    assert len(picked) == 1 and picked[0]["name"] in ("Ramp", "Burn & <Friends>")
    assert [d["name"] for d in client.get("/api/decks/random", params={"complete_only": True}).json()] == ["Ramp"]
    assert [d["id"] for d in client.get("/api/decks/random", params={"bracket": 2}).json()] == [first["id"]]
    assert len({d["id"] for d in client.get("/api/decks/random", params={"count": 2}).json()}) == 2
    too_many = client.get("/api/decks/random", params={"count": 3})
    assert too_many.status_code == 400 and "only 2 decks match" in too_many.json()["detail"]
    assert client.get(f"/api/decks/{first['id']}").json()["name"] == "Ramp"  # /decks/random does not shadow this


async def test_take_a_card_out_of_a_deck(session, scryfall):
    from sqlmodel import select

    from app.models import DeckEntry
    from app.services.decks import set_assigned

    await add_card_by_name(session, scryfall, "Sol Ring", quantity=1)
    first = save_deck(session, "First", [DeckCard("Sol Ring", 1), DeckCard("Fire // Ice", 1)])
    second = save_deck(session, "Second", [DeckCard("Sol Ring", 1)])
    entry = session.exec(select(DeckEntry).where(DeckEntry.deck_id == first.id, DeckEntry.name == "Sol Ring")).one()

    def sol(deck):
        return {row.name: row for row in saved_deck_rows(session, deck)}["Sol Ring"]

    assert (sol(first).missing, sol(second).missing) == (0, 1)

    # Taken out: still on the list, but missing, and the copy goes to the next deck that wants it.
    set_assigned(session, first, entry.id, False)
    row = sol(first)
    assert (row.unassigned, row.usable, row.missing, row.picks, row.used_by) == (True, 0, 1, [], ["Second"])
    assert sol(second).missing == 0
    assert [(s.deck.name, s.usable, s.needed) for s in deck_summaries(session)] == [("First", 0, 2), ("Second", 1, 1)]

    # Put back: the older deck gets its copy again.
    set_assigned(session, first, entry.id, True)
    assert (sol(first).missing, sol(second).missing) == (0, 1)

    with pytest.raises(DeckError):
        set_assigned(session, second, entry.id, False)  # a card of another deck


def test_remove_and_put_back_through_the_deck_page(client):
    client.post("/api/collection", json={"name": "Sol Ring"})
    deck = client.post("/api/decks", json={"name": "Ramp", "decklist": "1 Sol Ring\n1 Fire // Ice"}).json()
    sol = next(c for c in deck["cards"] if c["name"] == "Sol Ring")
    url = f"/decks/{deck['id']}/save"

    page = client.get(f"/decks/{deck['id']}")
    assert f'name="unassign" value="{sol["entry_id"]}"' in page.text
    assert page.text.count('name="unassign"') == 1  # no button for the card that is missing anyway

    # The button submits the whole form, so an edit made just before is saved along.
    removed = client.post(url, data={"bracket": "3", "unassign": str(sol["entry_id"])})
    assert "Sol Ring moved to Still missing" in removed.text
    assert "In this deck (0)" in removed.text and "Still missing (2)" in removed.text
    assert "taken out of this deck" in removed.text and f'name="assign" value="{sol["entry_id"]}"' in removed.text
    detail = client.get(f"/api/decks/{deck['id']}").json()
    card = next(c for c in detail["cards"] if c["name"] == "Sol Ring")
    assert (detail["bracket"], detail["cards_usable"], card["unassigned"], card["missing"], card["available"]) == (3, 0, True, 1, 1)

    back = client.post(url, data={"bracket": "3", "assign": str(sol["entry_id"])})
    assert "Sol Ring is back in this deck" in back.text and "In this deck (1)" in back.text

    api = f"/api/decks/{deck['id']}/cards/{sol['entry_id']}/assigned"
    assert client.put(api, json={"assigned": False}).json()["cards_usable"] == 0
    assert client.put(api, json={"assigned": True}).json()["cards_usable"] == 1
    assert client.put(f"/api/decks/{deck['id']}/cards/9999/assigned", json={"assigned": False}).status_code == 400
    assert "Nothing was saved" in client.post(url, data={"unassign": "9999"}).text


def test_unassigned_column_is_added_to_an_existing_database():
    from sqlalchemy import create_engine

    from app.database import add_missing_columns

    old = create_engine("sqlite://")
    with old.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE deck (id INTEGER PRIMARY KEY, name VARCHAR NOT NULL, bracket INTEGER)")
        connection.exec_driver_sql("CREATE TABLE deckentry (id INTEGER PRIMARY KEY, deck_id INTEGER, name VARCHAR, quantity INTEGER)")
        connection.exec_driver_sql("INSERT INTO deckentry (deck_id, name, quantity) VALUES (1, 'Sol Ring', 1)")
    add_missing_columns(old)
    add_missing_columns(old)
    with old.begin() as connection:
        assert connection.exec_driver_sql("SELECT name, unassigned FROM deckentry").all() == [("Sol Ring", 0)]


def test_put_back_is_offered_even_when_another_deck_took_the_copy(client):
    client.post("/api/collection", json={"name": "Sol Ring"})
    first = client.post("/api/decks", json={"name": "First", "decklist": "1 Sol Ring"}).json()
    second = client.post("/api/decks", json={"name": "Second", "decklist": "1 Sol Ring"}).json()
    entry = first["cards"][0]["entry_id"]

    removed = client.post(f"/decks/{first['id']}/save", data={"unassign": str(entry)})
    assert client.get(f"/api/decks/{second['id']}").json()["complete"] is True  # the copy moved on
    assert f'name="assign" value="{entry}"' in removed.text and "1 in use by Second" in removed.text

    client.post(f"/decks/{first['id']}/save", data={"assign": str(entry)})
    assert client.get(f"/api/decks/{first['id']}").json()["complete"] is True  # the older deck has it back
    assert client.get(f"/api/decks/{second['id']}").json()["complete"] is False


def test_decks_page_shows_how_many_decks_there_are(client):
    assert "· 0 saved" in client.get("/deck").text
    client.post("/api/collection", json={"name": "Sol Ring"})
    client.post("/api/decks", json={"name": "Ramp", "decklist": "1 Sol Ring"})
    client.post("/api/decks", json={"name": "Burn", "decklist": "4 Fire // Ice"})
    page = client.get("/deck").text
    assert "· 2 saved" in page and "Your saved decks (2)" in page
    assert "1 complete, 1 still missing cards" in page


def test_filter_decks_by_bracket(client):
    client.post("/api/collection", json={"name": "Sol Ring"})
    client.post("/api/decks", json={"name": "Ramp", "decklist": "1 Sol Ring", "bracket": 2})
    client.post("/api/decks", json={"name": "Burn", "decklist": "4 Fire // Ice", "bracket": 4})
    client.post("/api/decks", json={"name": "Brew", "decklist": "1 Fire // Ice"})

    page = client.get("/deck").text
    filter_html = page[page.index('id="deck-bracket-filter"'):page.index('id="deck-list"')]
    assert '<option value="2">2 · Core</option>' in filter_html and '<option value="4">4 · Optimized</option>' in filter_html
    assert '<option value="3">' not in filter_html  # only brackets you actually have decks in
    assert '<option value="none">No bracket</option>' in filter_html
    assert page.count('data-bracket="2" data-complete="1"') == 1 and page.count('data-bracket="none"') == 1

    assert [d["name"] for d in client.get("/api/decks", params={"bracket": 4}).json()] == ["Burn"]
    assert client.get("/api/decks", params={"bracket": 3}).json() == []
    assert len(client.get("/api/decks").json()) == 3


def test_basic_lands_count_towards_the_deck_size(client):
    # A 100-card Commander deck: 64 different cards you may or may not own, plus 36 basic lands.
    client.post("/api/collection", json={"name": "Sol Ring"})
    decklist = "1 Sol Ring\n63 Fire // Ice\n20 Forest\n16 Island"
    deck = client.post("/api/decks", json={"name": "Commander", "decklist": decklist}).json()
    assert (deck["cards_needed"], deck["cards_usable"], deck["basic_land_count"]) == (64, 1, 36)
    assert (deck["deck_size"], deck["deck_owned"], deck["complete"]) == (100, 37, False)

    listing = client.get("/deck").text
    assert ">37 / 100</td>" in listing  # the list counts the lands too

    page = client.get(f"/decks/{deck['id']}").text
    assert "37 of the 100 cards are set aside for this deck (37%)" in page
    assert "Deck size: <strong>100 cards</strong>" in page and "exactly 100, a full Commander deck" in page

    # Changing the lands changes the deck size straight away.
    client.put(f"/api/decks/{deck['id']}/basic-lands", json={"Forest": 20, "Island": 14})
    page = client.get(f"/decks/{deck['id']}").text
    assert "Deck size: <strong>98 cards</strong>" in page and "2 short of 100 for Commander" in page
    client.put(f"/api/decks/{deck['id']}/basic-lands", json={"Forest": 20, "Island": 19})
    assert "3 over 100 for Commander" in client.get(f"/decks/{deck['id']}").text

    checked = client.post("/deck", data={"decklist": decklist, "action": "check"}).text
    # The only Sol Ring is in the saved deck now, so a second deck gets the 36 lands and nothing else.
    assert "You have 36 of the 100 cards for this deck available (36%)" in checked


def test_add_cards_to_a_saved_deck(client):
    client.post("/api/collection", json={"set_code": "cmm", "collector_number": "410"})  # the pricier Sol Ring
    client.post("/api/collection", json={"name": "Sol Ring", "set_code": "cmm"})
    deck = client.post("/api/decks", json={"name": "Ramp", "decklist": "1 Fire // Ice"}).json()
    url = f"/decks/{deck['id']}/cards"

    page = client.get(f"/decks/{deck['id']}").text
    assert f'action="{url}"' in page and "Add a card to this deck" in page

    def cards():
        detail = client.get(f"/api/decks/{deck['id']}").json()
        return {c["name"]: c for c in detail["cards"]}, detail

    # By name: a card you own goes straight into "In this deck".
    added = client.post(url, data={"name": "Sol Ring", "quantity": "1"})
    assert "Added 1× Sol Ring to this deck" in added.text and "In this deck (1)" in added.text
    by_name, detail = cards()
    assert (by_name["Sol Ring"]["missing"], by_name["Sol Ring"]["chosen_collection_entry_id"]) == (0, None)
    assert detail["deck_size"] == 2

    # A card you do not own lands under "Still missing"; a typo is corrected by Scryfall.
    assert "Added 1× Lim-Dûl&#39;s Vault to this deck" in client.post(url, data={"name": "Lim-Dûl"}).text
    assert cards()[0]["Lim-Dûl's Vault"]["missing"] == 1

    # The same card again adds copies instead of a second line, also by one of its faces.
    again = client.post(url, data={"name": "Fire // Ice", "quantity": "2"})
    assert "Added 2× Fire // Ice to this deck (now 3 in this deck)" in again.text
    assert cards()[0]["Fire // Ice"]["needed"] == 3 and len(cards()[0]) == 3

    # By set + card number: that exact printing, if you own it, is the one the deck uses.
    client.post(f"/api/decks/{deck['id']}/cards", json={"set_code": "cmm", "collector_number": "410"})
    sol = cards()[0]["Sol Ring"]
    chosen = next(p for p in sol["printings"] if p["id"] == sol["chosen_collection_entry_id"])
    assert (sol["needed"], chosen["card"]["collector_number"]) == (2, "410")
    assert {u["printing"]["card"]["collector_number"] for u in sol["using"]} == {"1", "410"}  # needs both copies now

    # A basic land goes to the land counts, not to the card list.
    client.post(url, data={"name": "Forest", "quantity": "8"})  # not in the fake Scryfall -> not found
    assert "Forest" not in cards()[0]

    assert "Card not found" in client.post(url, data={"name": "Definitely Not A Card"}).text
    assert "Fill in a card name" in client.post(url, data={"name": ""}).text
    assert "Fill in the set" in client.post(url, data={"collector_number": "410"}).text
    api = f"/api/decks/{deck['id']}/cards"
    assert client.post(api, json={"name": "Definitely Not A Card"}).status_code == 404
    assert client.post(api, json={}).status_code == 400
    assert client.post(api, json={"name": "Sol Ring", "quantity": 0}).status_code == 422
    assert client.post("/api/decks/9999/cards", json={"name": "Sol Ring"}).status_code == 404
    assert len(client.get("/api/collection").json()["items"]) == 2  # the collection itself is untouched


async def test_adding_a_basic_land_to_a_deck_counts_it_as_a_land(session, fake_scryfall, scryfall):
    from app.services.decks import add_card, deck_left_out

    fake_scryfall.cards.append(scryfall_json("Forest", "id-forest", "blb", eur="0.10"))
    deck = save_deck(session, "Green", [DeckCard("Sol Ring", 1)])
    message = await add_card(session, scryfall, deck, "forest", quantity=8)
    assert message == "Added 8× Forest to the basic lands of this deck"
    await add_card(session, scryfall, deck, "Forest", quantity=2)
    assert deck_left_out(session, deck).basics == {"Forest": 10}
    assert [row.name for row in saved_deck_rows(session, deck)] == ["Sol Ring"]
