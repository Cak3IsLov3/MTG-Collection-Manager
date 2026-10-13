"""Decks imported from Archidekt: the printing of every card, and following the deck when it changes."""

import pytest

from app.services import decks as deck_service
from app.services.collection import add_card_by_name
from app.services.decks import (
    DeckError,
    apply_archidekt,
    check_deck,
    parse_archidekt_deck,
    parse_decklist,
    save_deck,
    saved_deck_rows,
    select_tracked,
    sync_all_decks,
)
from app.services.wishlist import wishlist_rows


def archidekt_card(name, set_code="tst", number="1", uid=None, quantity=1, foil=False, category="Main", **oracle):
    return {
        "quantity": quantity,
        "modifier": "Foil" if foil else "Normal",
        "categories": [category],
        "card": {
            "uid": uid,
            "collectorNumber": number,
            "edition": {"editioncode": set_code, "editionname": f"Set {set_code.upper()}"},
            "oracleCard": {"name": name, "types": ["Artifact"], **oracle},
        },
    }


def basic(name, quantity, set_code="blb", number="280"):
    return archidekt_card(name, set_code, number, quantity=quantity, category="Land", superTypes=["Basic"], types=["Land"])


def archidekt_deck(*cards, name="Rabbit Tribal", bracket=3):
    return {"name": name, "edhBracket": bracket, "cards": list(cards)}


@pytest.fixture
def archidekt(monkeypatch):
    """A fake Archidekt: `archidekt[deck id] = deck JSON`; unknown ids are private/deleted decks."""
    served: dict[int, dict] = {}

    async def fetch(deck_id, transport=None):
        if deck_id not in served:
            raise DeckError("Deck not found on Archidekt (is it private?)")
        return served[deck_id]

    monkeypatch.setattr(deck_service, "fetch_archidekt_json", fetch)
    return served


# --- the set of every card comes along --------------------------------------------


def test_archidekt_cards_carry_their_printing():
    _, cards = parse_archidekt_deck(
        archidekt_deck(
            archidekt_card("Sol Ring", "CMM", "410", uid="id-sol-cmm-art", foil=True),
            archidekt_card("Three Tree City", "pblb", "260s"),
            {"quantity": 1, "categories": ["Ramp"], "card": {"oracleCard": {"name": "Arcane Signet"}}},  # no printing
        )
    )
    assert [(c.name, c.set_code, c.collector_number, c.scryfall_id, c.foil) for c in cards] == [
        ("Sol Ring", "cmm", "410", "id-sol-cmm-art", True),
        ("Three Tree City", "pblb", "260s", None, False),
        ("Arcane Signet", None, None, None, None),
    ]


def test_text_decklist_keeps_the_set_too():
    cards = parse_decklist("1x Sol Ring (cmm) 410 *F* [Ramp]\n1 Fire // Ice (mh2)\n2 Lim-Dûl's Vault")
    assert [(c.name, c.set_code, c.collector_number, c.foil) for c in cards] == [
        ("Sol Ring", "cmm", "410", True), ("Fire // Ice", "mh2", None, None), ("Lim-Dûl's Vault", None, None, None),
    ]


async def test_deck_uses_the_printing_from_the_deck_list_when_you_own_it(session, scryfall):
    cheap = await add_card_by_name(session, scryfall, "Sol Ring", set_code="cmm")  # CMM #1, 1.50
    art = await add_card_by_name(session, scryfall, "", set_code="cmm", collector_number="410")  # 6.00
    _, cards = parse_archidekt_deck(archidekt_deck(archidekt_card("Sol Ring", "cmm", "410", uid="id-sol-cmm-art")))

    row = check_deck(session, cards)[0]
    assert (row.wanted_printing, row.uses_wanted_printing) == ("CMM #410", True)
    assert [(p.entry.id, n) for p, n in row.picks] == [(art.id, 1)]  # not the cheaper CMM #1

    # Without that printing the cheapest one is used, and the page says so.
    session.delete(art)
    session.flush()
    row = check_deck(session, cards)[0]
    assert ([p.entry.id for p, _ in row.picks], row.uses_wanted_printing) == ([cheap.id], False)


def test_saved_deck_shows_and_keeps_the_wanted_printing(client, archidekt):
    client.post("/api/collection", json={"name": "Sol Ring", "set_code": "cmm"})
    archidekt[77] = archidekt_deck(
        archidekt_card("Sol Ring", "lea", "1", uid="id-sol-lea"), archidekt_card("Fire // Ice", "mh2", "1", uid="id-fire", foil=True),
    )

    # "Only check" first: the printing survives the round trip through the save form.
    checked = client.post("/deck", data={"archidekt_url": "https://archidekt.com/decks/77/rabbits", "action": "check"}).text
    assert "deck list: LEA #1" in checked and "you use another printing" in checked
    assert "1 Sol Ring (lea) 1" in checked and "1 Fire // Ice (mh2) 1 *F*" in checked
    assert 'name="archidekt_id" value="77"' in checked
    assert 'name="cards" value="Fire // Ice|mh2|1|id-fire"' in checked

    saved = client.post("/deck/save", data={"name": "Rabbits", "decklist": "1 Sol Ring (lea) 1\n1 Fire // Ice (mh2) 1 *F*", "archidekt_id": "77"})
    assert "deck list: LEA #1" in saved.text and "deck list: MH2 #1 foil" in saved.text
    deck = client.get("/api/decks").json()[0]
    cards = {c["name"]: c for c in client.get(f"/api/decks/{deck['id']}").json()["cards"]}
    assert (cards["Sol Ring"]["wanted_set_code"], cards["Sol Ring"]["wanted_collector_number"]) == ("lea", "1")
    assert (cards["Sol Ring"]["uses_wanted_printing"], cards["Fire // Ice"]["wanted_foil"]) == (False, True)
    assert deck["archidekt_url"] == "https://archidekt.com/decks/77"


async def test_missing_cards_go_on_the_wishlist_in_the_printing_of_the_deck(session, scryfall):
    from app.scryfall import CardRef
    from app.services.decks import add_missing_to_wishlist

    added, not_found = await add_missing_to_wishlist(
        session, scryfall, [CardRef("Sol Ring", "cmm", "410", "id-sol-cmm-art"), "Fire // Ice", CardRef("Nope", "zzz")]
    )
    assert (added, not_found) == (2, ["Nope"])
    wanted = {row.card.name: row for row in wishlist_rows(session)}
    assert (wanted["Sol Ring"].card.collector_number, wanted["Sol Ring"].current_price) == ("410", 6.0)


def test_wishlist_button_passes_the_printing_along(client):
    response = client.post("/deck/wishlist", data={"cards": ["Sol Ring|cmm|410|id-sol-cmm-art", "Fire // Ice|||"], "target_pct": "90"})
    assert "Added 2 missing cards" in response.text
    items = {i["card"]["name"]: i for i in client.get("/api/wishlist").json()["items"]}
    assert items["Sol Ring"]["card"]["collector_number"] == "410" and items["Sol Ring"]["target_price"] == 5.4


# --- following an Archidekt deck ----------------------------------------------------


async def test_sync_takes_over_changes_made_on_archidekt(session, scryfall, archidekt):
    owned = await add_card_by_name(session, scryfall, "Sol Ring", set_code="cmm")
    await add_card_by_name(session, scryfall, "Fire // Ice")
    first = archidekt_deck(
        archidekt_card("Sol Ring", "cmm", "1"), archidekt_card("Fire // Ice", "mh2", "1"),
        archidekt_card("Lim-Dûl's Vault", "all", "1"), basic("Plains", 10),
    )
    _, cards = parse_archidekt_deck(first)
    tracked, left_out = select_tracked(cards)
    deck = save_deck(session, "Rabbit Tribal", tracked, left_out, bracket=3, archidekt_id=5)
    assert deck.synced_at is not None

    # The user makes two choices in the app that a sync must not undo.
    entries = {e.name: e for e in session.exec(deck_service.select(deck_service.DeckEntry)).all()}
    deck_service.choose_printing(session, deck, entries["Sol Ring"].id, owned.id)
    deck_service.set_assigned(session, deck, entries["Fire // Ice"].id, False)

    assert not apply_archidekt(session, deck, first).anything  # nothing changed on Archidekt

    # On Archidekt: the Vault is cut, Black Lotus is added, Sol Ring goes to another printing,
    # two more Plains, a token, a new name and a higher bracket.
    archidekt[5] = archidekt_deck(
        archidekt_card("Sol Ring", "lea", "1"), archidekt_card("Fire // Ice", "mh2", "1"),
        archidekt_card("Black Lotus", "lea", "1"), basic("Plains", 12),
        archidekt_card("Rabbit", "tblb", "21", quantity=3, category="Tokens & Extras", layout="token"),
        name="Rabbits Reborn", bracket=4,
    )
    result = await deck_service.sync_deck(session, deck)
    assert (result.added, result.removed, result.changed) == (["Black Lotus"], ["Lim-Dûl's Vault"], ["Sol Ring"])
    assert result.other == ["basic lands now 12", "tokens now 3", "bracket now 4", "renamed from Rabbit Tribal"]
    assert "Rabbits Reborn updated from Archidekt: added Black Lotus; removed Lim-Dûl's Vault; changed Sol Ring" in result.summary()

    rows = {row.name: row for row in saved_deck_rows(session, deck)}
    assert sorted(rows) == ["Black Lotus", "Fire // Ice", "Sol Ring"]
    assert (deck.name, deck.bracket) == ("Rabbits Reborn", 4)
    assert deck_service.deck_left_out(session, deck).basics == {"Plains": 12}
    assert deck_service.deck_left_out(session, deck).tokens == 3
    # Kept: the printing chosen for Sol Ring, and Fire // Ice still taken out of the deck.
    assert (rows["Sol Ring"].pinned_id, rows["Sol Ring"].wanted_printing) == (owned.id, "LEA #1")
    assert rows["Fire // Ice"].unassigned is True

    again = await deck_service.sync_deck(session, deck)
    assert not again.anything and "already up to date" in again.summary()


async def test_sync_all_skips_decks_without_a_link_and_survives_a_failure(session, archidekt):
    archidekt[1] = archidekt_deck(archidekt_card("Sol Ring"), archidekt_card("Fire // Ice"), name="Linked")
    linked = save_deck(session, "Linked", [deck_service.DeckCard("Sol Ring")], archidekt_id=1)
    gone = save_deck(session, "Gone", [deck_service.DeckCard("Sol Ring")], archidekt_id=404)  # deleted on Archidekt
    loose = save_deck(session, "No link", [deck_service.DeckCard("Sol Ring")])

    results = await sync_all_decks(session, pause=0)
    assert [(r.deck.name, r.added, r.error is not None) for r in results] == [("Linked", ["Fire // Ice"], False), ("Gone", [], True)]
    assert "could not sync" in results[1].summary()
    assert [row.name for row in saved_deck_rows(session, gone)] == ["Sol Ring"]  # left as it was
    assert [row.name for row in saved_deck_rows(session, loose)] == ["Sol Ring"]
    assert len(saved_deck_rows(session, linked)) == 2

    with pytest.raises(DeckError):
        await deck_service.sync_deck(session, loose)


def test_archidekt_link_on_the_deck_page(client, archidekt):
    client.post("/api/collection", json={"name": "Sol Ring"})
    archidekt[9] = archidekt_deck(archidekt_card("Sol Ring"), basic("Forest", 30), name="Mono Green", bracket=2)

    # Saving straight from a link remembers the link.
    saved = client.post("/deck", data={"archidekt_url": "https://archidekt.com/decks/9/mono-green", "action": "save"})
    assert "Saved Mono Green" in saved.text and 'value="https://archidekt.com/decks/9"' in saved.text
    assert "This deck follows its Archidekt deck" in saved.text and "Sync now" in saved.text
    deck = client.get("/api/decks").json()[0]
    assert (deck["archidekt_url"], deck["deck_size"], deck["bracket"]) == ("https://archidekt.com/decks/9", 31, 2)
    assert "↻ Archidekt" in client.get("/deck").text and "1 of 1 decks follow an Archidekt link" in client.get("/deck").text

    # The deck changes on Archidekt; "Sync now" and "Sync all" take it over.
    archidekt[9] = archidekt_deck(archidekt_card("Sol Ring"), archidekt_card("Fire // Ice"), basic("Forest", 29), name="Mono Green", bracket=2)
    synced = client.post(f"/decks/{deck['id']}/sync")
    assert "Mono Green updated from Archidekt: added Fire // Ice; basic lands now 29" in synced.text
    assert "already up to date" in client.post(f"/decks/{deck['id']}/sync").text
    assert "Checked 1 deck on Archidekt: 0 updated" in client.post("/deck/sync").text

    # Clearing the link stops following; an older deck can be given a link afterwards and is updated at once.
    cleared = client.post(f"/decks/{deck['id']}/save", data={"bracket": "2", "archidekt_url": ""})
    assert "no longer follows an Archidekt deck" in cleared.text and "Sync now" not in cleared.text
    assert "Could not sync" in client.post(f"/decks/{deck['id']}/sync").text
    archidekt[9]["cards"].append(archidekt_card("Lim-Dûl's Vault", "all", "1"))
    linked = client.post(f"/decks/{deck['id']}/save", data={"bracket": "2", "archidekt_url": "https://archidekt.com/decks/9"})
    assert "updated from Archidekt: added Lim-Dûl&#39;s Vault" in linked.text
    assert "Nothing was saved" in client.post(f"/decks/{deck['id']}/save", data={"archidekt_url": "https://example.com/x"}).text

    # A deck that was deleted or made private on Archidekt keeps what it has.
    del archidekt[9]
    assert "Could not sync: Deck not found on Archidekt" in client.post(f"/decks/{deck['id']}/sync").text
    assert "1 could not be reached (Mono Green)" in client.post("/deck/sync").text
    assert len(client.get(f"/api/decks/{deck['id']}").json()["cards"]) == 3


def test_archidekt_link_through_the_api(client, archidekt):
    archidekt[3] = archidekt_deck(archidekt_card("Sol Ring"), name="Api Deck")
    deck = client.post("/api/decks", json={"name": "Loose", "decklist": "1 Fire // Ice"}).json()
    assert (deck["archidekt_url"], deck["synced_at"]) == (None, None)
    assert client.post(f"/api/decks/{deck['id']}/sync").status_code == 400
    assert client.post("/api/decks/sync").json() == []

    linked = client.put(f"/api/decks/{deck['id']}/archidekt", json={"archidekt_url": "archidekt.com/decks/3"}).json()
    assert (linked["name"], linked["archidekt_url"], [c["name"] for c in linked["cards"]]) == ("Api Deck", "https://archidekt.com/decks/3", ["Sol Ring"])
    assert linked["synced_at"] is not None

    archidekt[3]["cards"].append(archidekt_card("Fire // Ice"))
    one = client.post(f"/api/decks/{deck['id']}/sync").json()
    assert (one["added"], one["error"]) == (["Fire // Ice"], None)
    everything = client.post("/api/decks/sync").json()
    assert [(r["name"], r["added"]) for r in everything] == [("Api Deck", [])]

    assert client.put(f"/api/decks/{deck['id']}/archidekt", json={"archidekt_url": "https://moxfield.com/decks/x"}).status_code == 400
    assert client.put(f"/api/decks/{deck['id']}/archidekt", json={"archidekt_url": None}).json()["archidekt_url"] is None
    from_link = client.post("/api/decks", json={"name": "From link", "archidekt_url": "https://archidekt.com/decks/3"}).json()
    assert from_link["archidekt_url"] == "https://archidekt.com/decks/3"


def test_new_columns_are_added_to_an_existing_database():
    from sqlalchemy import create_engine

    from app.database import add_missing_columns

    old = create_engine("sqlite://")
    with old.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE deck (id INTEGER PRIMARY KEY, name VARCHAR NOT NULL, bracket INTEGER)")
        connection.exec_driver_sql("CREATE TABLE deckentry (id INTEGER PRIMARY KEY, deck_id INTEGER, name VARCHAR, quantity INTEGER, unassigned BOOLEAN NOT NULL DEFAULT 0)")
        connection.exec_driver_sql("INSERT INTO deck (name) VALUES ('Marwyn')")
        connection.exec_driver_sql("INSERT INTO deckentry (deck_id, name, quantity) VALUES (1, 'Sol Ring', 1)")
    add_missing_columns(old)
    add_missing_columns(old)
    with old.begin() as connection:
        assert connection.exec_driver_sql("SELECT name, archidekt_id, synced_at FROM deck").all() == [("Marwyn", None, None)]
        assert connection.exec_driver_sql("SELECT name, set_code, collector_number, scryfall_id, foil FROM deckentry").all() == [
            ("Sol Ring", None, None, None, None)
        ]


async def test_the_printing_a_deck_list_names_goes_to_that_deck(session, scryfall):
    """The Arcane Signet case: a copy that an older deck merely happened to take moves to the
    deck whose list names exactly that printing."""
    from app.services.decks import DeckCard, deck_usage

    cheap = await add_card_by_name(session, scryfall, "Sol Ring", set_code="cmm")  # CMM #1, 1.50
    art = await add_card_by_name(session, scryfall, "", set_code="cmm", collector_number="410")  # 6.00
    older = save_deck(session, "Older", [DeckCard("Sol Ring")])  # any Sol Ring will do
    named = save_deck(session, "Named", [DeckCard("Sol Ring", set_code="cmm", collector_number="1")])

    def used(deck):
        return [(p.entry.id, n) for p, n in saved_deck_rows(session, deck)[0].picks]

    # Without the rule the older deck would take the cheapest copy, CMM #1, which is the one "Named" lists.
    assert (used(named), used(older)) == ([(cheap.id, 1)], [(art.id, 1)])
    assert saved_deck_rows(session, named)[0].uses_wanted_printing
    assert {entry: [d.name for d, _ in decks] for entry, decks in deck_usage(session).items()} == {
        cheap.id: ["Named"], art.id: ["Older"],
    }

    # With a single copy it goes to the deck that names it; the older deck is the one that misses it.
    session.delete(art)
    session.flush()
    assert (used(named), saved_deck_rows(session, older)[0].missing) == ([(cheap.id, 1)], 1)

    # Two decks naming the same printing: the older one first. A printing you chose yourself beats both.
    also = save_deck(session, "Also named", [DeckCard("Sol Ring", set_code="cmm", collector_number="1")])
    assert (used(named), used(also)) == ([(cheap.id, 1)], [])
    entry = session.exec(deck_service.select(deck_service.DeckEntry).where(deck_service.DeckEntry.deck_id == older.id)).one()
    deck_service.choose_printing(session, older, entry.id, cheap.id)
    assert (used(older), used(named), used(also)) == ([(cheap.id, 1)], [], [])
