import pytest

from app.models import Deck
from app.services import decks
from app.services.collection import add_card_by_name, collection_rows
from app.services.decks import DeckCard, DeckError, deck_summaries, save_deck, saved_deck_rows


async def test_a_testing_deck_takes_no_cards_from_the_collection(session, scryfall):
    await add_card_by_name(session, scryfall, "Sol Ring", quantity=1)
    testing = save_deck(session, "Try-out", [DeckCard("Sol Ring", 1)])
    normal = save_deck(session, "Real", [DeckCard("Sol Ring", 1)])
    session.commit()
    # The older deck gets the only copy ...
    assert saved_deck_rows(session, testing)[0].usable == 1
    assert saved_deck_rows(session, normal)[0].usable == 0

    # ... until it is a testing deck: it keeps nothing, the other deck gets the copy.
    decks.set_testing(session, testing, True)
    session.commit()
    assert saved_deck_rows(session, testing)[0].usable == 0
    assert saved_deck_rows(session, normal)[0].usable == 1
    assert {s.deck.name: s.usable for s in deck_summaries(session)} == {"Try-out": 0, "Real": 1}

    decks.set_testing(session, testing, False)
    session.commit()
    assert saved_deck_rows(session, testing)[0].usable == 1


async def test_folders_hold_decks_and_go_away_without_them(session, scryfall):
    deck = save_deck(session, "Elves", [DeckCard("Sol Ring", 1)])
    folder = decks.create_folder(session, "  Tribal  ")
    assert folder.name == "Tribal"
    with pytest.raises(DeckError):
        decks.create_folder(session, "tribal")
    with pytest.raises(DeckError):
        decks.create_folder(session, "   ")
    other = decks.create_folder(session, "Budget")
    with pytest.raises(DeckError):
        decks.rename_folder(session, other, "TRIBAL")
    decks.rename_folder(session, other, "Cheap")

    decks.set_folder(session, deck, folder.id)
    assert session.get(Deck, deck.id).folder_id == folder.id
    with pytest.raises(DeckError):
        decks.set_folder(session, deck, 999)

    assert decks.delete_folder(session, folder) == 1
    session.commit()
    assert session.get(Deck, deck.id).folder_id is None  # the deck stays
    assert [f.name for f in decks.deck_folders(session)] == ["Cheap"]


async def test_add_all_cards_puts_back_what_was_taken_out_and_new_copies(session, scryfall):
    from sqlmodel import select

    from app.models import CollectionEntry, DeckEntry

    await add_card_by_name(session, scryfall, "Sol Ring", quantity=1)
    deck = save_deck(session, "Mine", [DeckCard("Sol Ring", 1), DeckCard("Fire // Ice", 1)])
    session.commit()
    entries = {e.name: e for e in session.exec(select(DeckEntry).where(DeckEntry.deck_id == deck.id))}
    decks.set_assigned(session, deck, entries["Sol Ring"].id, False)
    session.commit()
    rows = {r.name: r for r in saved_deck_rows(session, deck)}
    assert rows["Sol Ring"].missing == 1 and rows["Sol Ring"].can_add and not rows["Fire // Ice"].can_add
    assert rows["Fire // Ice"].missing == 1  # not owned at all

    # The one taken out comes back; the one you do not own is added to the collection.
    result = await decks.add_all_missing(session, scryfall, deck)
    session.commit()
    assert (result.put_back, result.added, result.new_copies, result.not_found) == (1, 1, 0, [])
    assert all(r.missing == 0 for r in saved_deck_rows(session, deck))
    assert {r.card.name: r.entry.quantity for r in collection_rows(session)} == {"Sol Ring": 1, "Fire // Ice": 1}
    assert (await decks.add_all_missing(session, scryfall, deck)).nothing

    # New copies from an import that wait go in first, instead of buying more.
    decks.set_assigned(session, deck, entries["Sol Ring"].id, False)
    session.commit()
    for owned in session.exec(select(CollectionEntry)):
        owned.unsorted = owned.quantity
        session.add(owned)
    session.commit()
    result = await decks.add_all_missing(session, scryfall, deck)
    session.commit()
    assert (result.new_copies, result.added) == (2, 0)
    assert all(r.missing == 0 for r in saved_deck_rows(session, deck))


async def test_a_testing_deck_shows_what_it_would_get_and_cost_and_can_be_made_real(session, scryfall):
    await add_card_by_name(session, scryfall, "Sol Ring", quantity=1)
    deck = save_deck(session, "Try", [DeckCard("Sol Ring", 1), DeckCard("Fire // Ice", 1)])
    decks.set_testing(session, deck, True)
    session.commit()
    assert [s.usable for s in deck_summaries(session)] == [0]

    preview = decks.real_preview(session, deck)
    assert (preview.needed, preview.usable, preview.missing, preview.percent) == (2, 1, 1, 50)
    assert [row.name for row in preview.missing_rows] == ["Fire // Ice"]
    assert [s.usable for s in deck_summaries(session)] == [0]  # a preview takes nothing

    cost = await decks.real_cost(session, scryfall, deck)
    assert cost.cards == 1 and cost.cost > 0 and cost.unpriced == []

    decks.set_testing(session, deck, False)
    session.commit()
    assert [s.usable for s in deck_summaries(session)] == [1]


def test_make_real_and_the_cost_through_the_pages(client):
    client.post("/api/collection", json={"name": "Sol Ring"})
    client.post("/deck", data={"decklist": "1 Sol Ring\n1 Fire // Ice", "deck_name": "Try", "action": "save", "testing": "on"})
    deck_id = client.get("/api/decks").json()[0]["id"]

    page = client.get(f"/decks/{deck_id}").text
    assert "Make this a real deck" in page and "What would it cost?" in page
    assert client.get(f"/decks/{deck_id}/real-cost").json()["cards"] == 1

    assert client.post(f"/decks/{deck_id}/make-real").status_code == 200
    page = client.get(f"/decks/{deck_id}").text
    assert "Make this a real deck" not in page
    assert client.get(f"/decks/999/real-cost").status_code == 404


def test_a_deck_can_be_added_as_a_testing_deck(client):
    client.post("/api/collection", json={"name": "Sol Ring"})
    page = client.get("/deck").text
    assert 'name="testing"' in page

    # Added with the box ticked, in one go and after a check.
    client.post("/deck", data={"decklist": "1 Sol Ring", "deck_name": "Try", "action": "save", "testing": "on"})
    checked = client.post("/deck", data={"decklist": "1 Sol Ring", "deck_name": "Later", "action": "check", "testing": "on"}).text
    assert 'name="testing" type="checkbox" checked' in checked
    client.post("/deck/save", data={"name": "Later", "decklist": "1 Sol Ring", "testing": "on"})
    client.post("/deck", data={"decklist": "1 Sol Ring", "deck_name": "Real", "action": "save"})

    listed = {d["name"]: d for d in client.get("/api/decks").json()}
    deck_page = {name: client.get(f"/decks/{d['id']}").text for name, d in listed.items()}
    for name, expected in (("Try", True), ("Later", True), ("Real", False)):
        box = deck_page[name].split('name="testing"')[1].split(">")[0]
        assert ("checked" in box) is expected, name


def test_folders_and_testing_through_the_pages(client):
    client.post("/api/collection", json={"name": "Sol Ring"})
    client.post("/deck", data={"decklist": "1 Sol Ring", "deck_name": "Mine", "action": "save"})
    deck_id = client.get("/api/decks").json()[0]["id"]

    assert client.post("/deck/folders", data={"name": "Tribal"}).status_code == 200
    page = client.get("/deck").text
    assert "Tribal" in page and 'id="deck-folder-filter"' in page

    folder_id = int(page.split('action="/deck/folders/')[1].split("/")[0])
    client.post(f"/decks/{deck_id}/folder", data={"folder": str(folder_id)})
    assert f'data-folder="{folder_id}"' in client.get("/deck").text

    # The deck page has the folder menu and the testing box, saved with the other settings.
    client.post(f"/decks/{deck_id}/save", data={"tokens_shown": "1", "testing": "on", "folder": ""})
    deck_page = client.get(f"/decks/{deck_id}").text
    assert 'name="testing"' in deck_page and "checked" in deck_page.split('name="testing"')[1].split(">")[0]
    assert "testing</span>" in client.get("/deck").text

    client.post(f"/deck/folders/{folder_id}/delete")
    assert "Tribal" not in client.get("/deck").text
