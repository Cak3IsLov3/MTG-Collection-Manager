import datetime as dt

from sqlmodel import select

from app.models import Card, PriceSnapshot
from tests.test_csv_import import MOXFIELD


def test_add_and_list_collection(client):
    response = client.post("/api/collection", json={"name": "Sol Ring", "quantity": 2})
    assert response.status_code == 201
    body = response.json()
    assert (body["card"]["name"], body["quantity"], body["unit_price"], body["total_price"]) == ("Sol Ring", 2, 1.5, 3.0)

    listing = client.get("/api/collection").json()
    assert listing["total_value"] == 3.0 and len(listing["items"]) == 1


def test_same_printing_is_merged_and_foil_is_separate(client):
    client.post("/api/collection", json={"name": "Sol Ring"})
    client.post("/api/collection", json={"name": "Sol Ring", "quantity": 2})
    client.post("/api/collection", json={"name": "Sol Ring", "is_foil": True})
    items = client.get("/api/collection").json()
    assert sorted((i["quantity"], i["is_foil"], i["unit_price"]) for i in items["items"]) == [(1, True, 4.0), (3, False, 1.5)]
    assert items["total_value"] == 8.5


def test_special_character_names(client):
    for name in ("Lim-Dûl's Vault", "Fire // Ice"):
        assert client.post("/api/collection", json={"name": name}).json()["card"]["name"] == name


def test_null_price_is_handled(client):
    body = client.post("/api/collection", json={"name": "Black Lotus"}).json()
    assert body["unit_price"] is None and body["total_price"] is None
    assert client.get("/api/collection").json()["total_value"] == 0.0
    assert client.get("/").status_code == 200


def test_unknown_card_is_404_and_bad_input_422(client):
    assert client.post("/api/collection", json={"name": "Definitely Not A Card"}).status_code == 404
    assert client.post("/api/collection", json={"name": "Sol Ring", "quantity": 0}).status_code == 422


def test_csv_import_endpoint(client):
    response = client.post("/api/collection/import", files={"file": ("cards.csv", MOXFIELD.encode(), "text/csv")})
    assert response.status_code == 200
    assert response.json() == {
        "format": "moxfield", "imported_rows": 2, "imported_cards": 4, "not_found": [], "errors": [], "kept_manual": 0, "new_copies": 4,
    }
    assert client.get("/api/collection").json()["total_value"] == 5.3  # 3 x 1.50 + foil 0.80

    bad = client.post("/api/collection/import", files={"file": ("x.csv", b"foo,bar\n1,2\n", "text/csv")})
    assert bad.status_code == 400


def test_wishlist_add_and_list(client):
    response = client.post("/api/wishlist", json={"name": "Lim-Dûl's Vault", "target_price": 15})
    assert response.status_code == 201
    body = response.json()
    assert (body["current_price"], body["price_source"], body["below_target"]) == (12.34, "scryfall", True)
    assert body["cardmarket_url"].startswith("https://www.cardmarket.com/")  # defaulted from Scryfall

    client.post("/api/wishlist", json={"name": "Lim-Dûl's Vault", "target_price": 10})  # updates the target
    listing = client.get("/api/wishlist").json()
    assert listing["total_cost"] == 12.34
    assert [(i["target_price"], i["below_target"]) for i in listing["items"]] == [(10.0, False)]


def test_wishlist_rejects_foreign_urls(client):
    payload = {"name": "Sol Ring", "target_price": 1, "cardmarket_url": "https://example.com/x"}
    assert client.post("/api/wishlist", json=payload).status_code == 400


def test_purchase_moves_card_from_want_to_have(client):
    entry_id = client.post("/api/wishlist", json={"name": "Sol Ring", "target_price": 1}).json()["id"]
    bought = client.post(f"/api/wishlist/{entry_id}/purchase")
    assert bought.status_code == 200 and bought.json()["quantity"] == 1

    assert client.get("/api/wishlist").json()["items"] == []
    assert client.get("/api/collection").json()["total_value"] == 1.5
    assert client.post(f"/api/wishlist/{entry_id}/purchase").status_code == 404  # already bought

    summary = client.get("/api/summary").json()
    assert (summary["collection_value"], summary["wishlist_cost"], summary["card_count"]) == (1.5, 0.0, 1)


def test_delete_collection_entry(client):
    entry_id = client.post("/api/collection", json={"name": "Sol Ring"}).json()["id"]
    assert client.delete(f"/api/collection/{entry_id}").status_code == 204
    assert client.delete(f"/api/collection/{entry_id}").status_code == 404
    assert client.get("/api/collection").json()["items"] == []


def test_dashboard_shows_totals_and_movers(client, session):
    client.post("/api/collection", json={"name": "Sol Ring", "quantity": 2})
    client.post("/api/wishlist", json={"name": "Fire // Ice", "target_price": 0.1})
    card = session.exec(select(Card).where(Card.name == "Sol Ring")).one()
    session.add(PriceSnapshot(card_id=card.id, date=dt.date.today() - dt.timedelta(days=3), source="scryfall", price_eur=1.0))
    session.commit()

    page = client.get("/")
    assert page.status_code == 200
    assert "€3.00" in page.text and "€0.25" in page.text
    assert "+0.50 (+50.0%)" in page.text  # Sol Ring went 1.00 -> 1.50 this week


def test_pages_render(client):
    card_id = client.post("/api/collection", json={"name": "Lim-Dûl's Vault"}).json()["card"]["id"]
    client.post("/api/wishlist", json={"name": "Fire // Ice", "target_price": 1})
    for path in ("/", "/collection", "/wishlist", "/deck", f"/cards/{card_id}"):
        assert client.get(path).status_code == 200, path
    assert client.get("/cards/9999").status_code == 404
    assert client.get(f"/api/cards/{card_id}/prices").json()["scryfall"] == [12.34]


def test_html_forms(client):
    added = client.post("/collection/add", data={"name": "Sol Ring", "quantity": "2", "is_foil": "on"})
    assert added.status_code == 200 and "Added Sol Ring" in added.text  # followed the redirect
    assert client.get("/api/collection").json()["items"][0]["is_foil"] is True

    missing = client.post("/collection/add", data={"name": "Definitely Not A Card"})
    assert "Card not found" in missing.text

    watched = client.post("/wishlist/add", data={"name": "Fire // Ice", "target_price": "0,50"})
    assert "Watching Fire // Ice" in watched.text
    entry = client.get("/api/wishlist").json()["items"][0]
    assert entry["target_price"] == 0.5

    moved = client.post(f"/wishlist/{entry['id']}/purchase")
    assert "moved from wishlist to collection" in moved.text


def test_search_collection(client):
    for name in ("Sol Ring", "Lim-Dûl's Vault", "Fire // Ice"):
        client.post("/api/collection", json={"name": name})

    def found(query):
        return [item["card"]["name"] for item in client.get("/api/collection", params={"q": query}).json()["items"]]

    assert found("sol") == ["Sol Ring"]
    assert found("lim-dul vault") == ["Lim-Dûl's Vault"]  # accents and word order do not matter
    assert found("MH2") == ["Fire // Ice"]  # set code
    assert found("ring cmm") == ["Sol Ring"] and found("ring mh2") == []
    assert len(found("")) == 3

    page = client.get("/collection", params={"q": "sol"})
    assert 'id="collection-search"' in page.text and 'value="sol"' in page.text
    assert page.text.count("data-search=") == 3  # filtering happens in the browser


def test_change_quantity(client):
    entry = client.post("/api/collection", json={"name": "Sol Ring", "quantity": 4}).json()
    updated = client.patch(f"/api/collection/{entry['id']}", json={"quantity": 3})
    assert updated.status_code == 200
    assert (updated.json()["quantity"], updated.json()["total_price"]) == (3, 4.5)
    assert client.get("/api/collection").json()["total_value"] == 4.5
    assert client.get("/api/summary").json()["card_count"] == 3

    assert client.patch(f"/api/collection/{entry['id']}", json={"quantity": 0}).status_code == 422
    assert client.patch("/api/collection/9999", json={"quantity": 1}).status_code == 404

    page = client.get("/collection")
    assert 'class="qty"' in page.text and 'value="3"' in page.text
    assert "Quantity updated" in client.post(f"/collection/{entry['id']}/quantity", data={"quantity": "2"}).text
    assert "at least 1" in client.post(f"/collection/{entry['id']}/quantity", data={"quantity": "0"}).text
    assert client.get("/api/collection").json()["items"][0]["quantity"] == 2


async def test_lowering_a_quantity_frees_fewer_copies_for_decks(session, scryfall):
    from app.services.collection import add_card_by_name, set_quantity
    from app.services.decks import DeckCard, save_deck, saved_deck_rows

    entry = await add_card_by_name(session, scryfall, "Sol Ring", quantity=2)
    first = save_deck(session, "First", [DeckCard("Sol Ring", 1)])
    second = save_deck(session, "Second", [DeckCard("Sol Ring", 1)])
    assert saved_deck_rows(session, second)[0].missing == 0
    set_quantity(session, entry, 1)
    assert (saved_deck_rows(session, first)[0].missing, saved_deck_rows(session, second)[0].missing) == (0, 1)


def test_add_exact_printing_by_set_and_collector_number(client):
    # Commander Masters has two Sol Rings in the fake data: #1 (1.50) and #410 (6.00).
    added = client.post("/api/collection", json={"set_code": "CMM", "collector_number": "410"})
    assert added.status_code == 201
    card = added.json()["card"]
    assert (card["name"], card["collector_number"], added.json()["unit_price"]) == ("Sol Ring", "410", 6.0)

    by_name = client.post("/api/collection", json={"name": "Sol Ring", "set_code": "cmm"}).json()
    assert by_name["card"]["collector_number"] == "1"  # a name alone cannot pick the other version
    assert len(client.get("/api/collection").json()["items"]) == 2
    assert [i["card"]["collector_number"] for i in client.get("/api/collection", params={"q": "#410"}).json()["items"]] == ["410"]

    # The name is optional, but when given it has to match the card behind the number.
    assert client.post("/api/collection", json={"name": "sol ring", "set_code": "cmm", "collector_number": "410"}).status_code == 201
    wrong = client.post("/api/collection", json={"name": "Black Lotus", "set_code": "cmm", "collector_number": "410"})
    assert wrong.status_code == 400 and "is Sol Ring, not Black Lotus" in wrong.json()["detail"]
    assert client.post("/api/collection", json={"collector_number": "410"}).status_code == 400  # no set
    assert client.post("/api/collection", json={}).status_code == 400
    assert client.post("/api/collection", json={"set_code": "cmm", "collector_number": "999"}).status_code == 404

    form = client.post("/collection/add", data={"set_code": "cmm", "collector_number": "#410", "quantity": "1"})
    assert "Added Sol Ring (CMM #410)" in form.text and "(CMM #410)" in client.get("/collection").text
    assert "Fill in the set" in client.post("/collection/add", data={"name": "Sol Ring", "collector_number": "410"}).text


def test_wishlist_table_is_sortable(client):
    client.post("/api/wishlist", json={"name": "Lim-Dûl's Vault", "target_price": 10})
    client.post("/api/wishlist", json={"name": "Black Lotus", "target_price": 500})  # no price in the fake data
    page = client.get("/wishlist").text
    for key in ("name", "current", "target", "difference"):
        assert f'data-sort="{key}"' in page
    assert 'data-name="Lim-Dûl&#39;s Vault" data-current="12.34"' in page
    assert 'data-target="10.0" data-difference="2.34"' in page
    assert 'data-name="Black Lotus" data-current=""' in page  # sorts last, whatever the direction


def test_save_all_target_prices_at_once(client):
    vault = client.post("/api/wishlist", json={"name": "Lim-Dûl's Vault", "target_price": 10}).json()
    sol = client.post("/api/wishlist", json={"name": "Sol Ring", "target_price": 1}).json()
    fire = client.post("/api/wishlist", json={"name": "Fire // Ice", "target_price": 0.2}).json()

    page = client.get("/wishlist").text
    assert 'id="wishlist-form"' in page and "Save all prices" in page
    assert f'name="target:{vault["id"]}" form="wishlist-form"' in page
    assert page.count(">Save</button>") == 0  # no more saving row by row
    for pct in (80, 85, 90, 95):  # fill every target with a share of the current price
        assert f'data-target-pct="{pct}"' in page

    def targets():
        return {i["card"]["name"]: i["target_price"] for i in client.get("/api/wishlist").json()["items"]}

    # Three fields are submitted, two of them changed (one with a comma).
    saved = client.post("/wishlist/targets", data={
        f"target:{vault['id']}": "8,50", f"target:{sol['id']}": "1.00", f"target:{fire['id']}": "0.10",
    })
    assert "Saved 2 target prices" in saved.text
    assert targets() == {"Lim-Dûl's Vault": 8.5, "Sol Ring": 1.0, "Fire // Ice": 0.1}
    assert "No target prices were changed" in client.post("/wishlist/targets", data={f"target:{sol['id']}": "1"}).text
    assert "Saved 1 target price<" in client.post("/wishlist/targets", data={f"target:{sol['id']}": "2"}).text

    # One bad value and nothing is stored, not even the valid ones next to it.
    failed = client.post("/wishlist/targets", data={f"target:{vault['id']}": "5", f"target:{sol['id']}": "cheap"})
    assert "Nothing was saved" in failed.text
    assert "Nothing was saved" in client.post("/wishlist/targets", data={f"target:{vault['id']}": "5", "target:9999": "1"}).text
    assert targets() == {"Lim-Dûl's Vault": 8.5, "Sol Ring": 2.0, "Fire // Ice": 0.1}

    api = client.put("/api/wishlist/targets", json={str(vault["id"]): 7, str(fire["id"]): 0.3})
    assert api.status_code == 200 and targets() == {"Lim-Dûl's Vault": 7.0, "Sol Ring": 2.0, "Fire // Ice": 0.3}
    assert client.put("/api/wishlist/targets", json={str(vault["id"]): -1}).status_code == 400
    assert client.put("/api/wishlist/targets", json={"9999": 1}).status_code == 400


def test_choose_the_set_of_a_wishlist_card(client):
    entry = client.post("/api/wishlist", json={"name": "Sol Ring", "set_code": "cmm", "target_price": 1.2}).json()
    assert (entry["card"]["set_code"], entry["card"]["collector_number"], entry["current_price"]) == ("cmm", "1", 1.5)

    # The page offers "Change set"; the printings are loaded on demand.
    page = client.get("/wishlist").text
    assert f'action="/wishlist/{entry["id"]}/printing"' in page and "Change set" in page
    printings = client.get("/api/printings", params={"name": "Sol Ring"}).json()
    assert sorted((p["set_code"], p["collector_number"], p["price_eur"]) for p in printings) == [
        ("cmm", "1", 1.5), ("cmm", "410", 6.0), ("lea", "1", 900.0),
    ]
    assert client.get("/api/printings", params={"name": "Definitely Not A Card"}).json() == []

    lea = next(p for p in printings if p["set_code"] == "lea")
    changed = client.post(f"/wishlist/{entry['id']}/printing", data={"scryfall_id": lea["scryfall_id"]})
    assert "Now watching Sol Ring from Set LEA (LEA)" in changed.text
    item = client.get("/api/wishlist").json()["items"][0]
    assert (item["id"], item["card"]["set_code"], item["current_price"], item["target_price"]) == (entry["id"], "lea", 900.0, 1.2)
    assert lea["scryfall_id"] in item["cardmarket_url"]  # the Cardmarket link follows the printing
    assert client.get("/api/wishlist").json()["total_cost"] == 900.0

    # Another card, or a printing that is already being watched, is refused.
    vault = client.get("/api/printings", params={"name": "Lim-Dûl's Vault"}).json()[0]
    wrong = client.put(f"/api/wishlist/{entry['id']}/printing", json={"scryfall_id": vault["scryfall_id"]})
    assert wrong.status_code == 400 and "is not a printing of Sol Ring" in wrong.json()["detail"]
    second = client.post("/api/wishlist", json={"name": "Sol Ring", "set_code": "cmm", "target_price": 1}).json()
    duplicate = client.put(f"/api/wishlist/{second['id']}/printing", json={"scryfall_id": lea["scryfall_id"]})
    assert duplicate.status_code == 400 and "already on your wishlist" in duplicate.json()["detail"]
    assert client.put(f"/api/wishlist/{entry['id']}/printing", json={"scryfall_id": "nope"}).status_code == 404
    assert client.put("/api/wishlist/9999/printing", json={"scryfall_id": lea["scryfall_id"]}).status_code == 404
    assert "Choose a set first" in client.post(f"/wishlist/{entry['id']}/printing", data={}).text

    art = next(p for p in printings if p["collector_number"] == "410")
    api = client.put(f"/api/wishlist/{entry['id']}/printing", json={"scryfall_id": art["scryfall_id"]}).json()
    assert (api["card"]["collector_number"], api["current_price"], api["target_price"]) == ("410", 6.0, 1.2)


def test_remove_all_from_the_wishlist(client):
    assert "Remove all" not in client.get("/wishlist").text  # nothing to remove yet
    for name in ("Sol Ring", "Fire // Ice", "Lim-Dûl's Vault"):
        client.post("/api/wishlist", json={"name": name, "target_price": 1})
    client.post("/api/collection", json={"name": "Sol Ring"})

    page = client.get("/wishlist").text
    assert 'action="/wishlist/clear"' in page and 'data-confirm="Click again to remove all 3 cards"' in page

    cleared = client.post("/wishlist/clear")
    assert "Removed all 3 cards from your wishlist" in cleared.text and "Nothing on your wishlist yet." in cleared.text
    assert client.get("/api/wishlist").json() == {"total_cost": 0.0, "items": []}
    summary = client.get("/api/summary").json()
    assert (summary["wishlist_count"], summary["wishlist_cost"], summary["card_count"]) == (0, 0.0, 1)  # collection untouched
    assert "already empty" in client.post("/wishlist/clear").text

    # A card can simply be watched again afterwards.
    client.post("/api/wishlist", json={"name": "Sol Ring", "target_price": 2})
    client.post("/api/wishlist", json={"name": "Fire // Ice", "target_price": 2})
    assert client.delete("/api/wishlist").json() == {"removed": 2}
    assert client.delete("/api/wishlist").json() == {"removed": 0}


def test_mark_a_collection_card_as_foil(client):
    entry = client.post("/api/collection", json={"name": "Sol Ring", "set_code": "cmm", "quantity": 2}).json()
    assert (entry["is_foil"], entry["unit_price"]) == (False, 1.5)
    url = f"/api/collection/{entry['id']}"

    page = client.get("/collection").text
    assert f'class="foil" data-entry="{entry["id"]}"' in page and "checked" not in page.split('class="foil"')[1][:80]

    foil = client.patch(url, json={"is_foil": True}).json()
    assert (foil["id"], foil["is_foil"], foil["quantity"], foil["unit_price"], foil["total_price"]) == (entry["id"], True, 2, 4.0, 8.0)
    assert client.get("/api/collection").json()["total_value"] == 8.0
    assert "checked" in client.get("/collection").text.split('class="foil"')[1][:80]

    back = client.patch(url, json={"is_foil": False, "quantity": 3}).json()  # both at once
    assert (back["is_foil"], back["quantity"], back["total_price"]) == (False, 3, 4.5)
    assert client.patch(url, json={"is_foil": False}).json()["quantity"] == 3  # no change is fine

    assert client.patch(url, json={}).status_code == 400
    assert client.patch("/api/collection/9999", json={"is_foil": True}).status_code == 404


def test_marking_foil_merges_with_the_foil_copies_you_already_have(client):
    regular = client.post("/api/collection", json={"name": "Sol Ring", "set_code": "cmm", "quantity": 2}).json()
    foil = client.post("/api/collection", json={"name": "Sol Ring", "set_code": "cmm", "is_foil": True}).json()
    deck = client.post("/api/decks", json={"name": "Ramp", "decklist": "1 Sol Ring"}).json()
    card = deck["cards"][0]
    client.put(f"/api/decks/{deck['id']}/cards/{card['entry_id']}/printing", json={"collection_entry_id": regular["id"]})

    merged = client.patch(f"/api/collection/{regular['id']}", json={"is_foil": True}).json()
    assert (merged["id"], merged["quantity"], merged["is_foil"]) == (foil["id"], 3, True)
    items = client.get("/api/collection").json()["items"]
    assert len(items) == 1 and items[0]["total_price"] == 12.0

    # The deck that had picked those copies follows them to the merged entry.
    after = client.get(f"/api/decks/{deck['id']}").json()["cards"][0]
    assert (after["chosen_collection_entry_id"], after["missing"]) == (foil["id"], 0)


def test_foil_without_a_foil_price_borrows_one(client, fake_scryfall):
    from tests.conftest import scryfall_json

    fake_scryfall.cards.append(scryfall_json("Lim-Dûl's Vault", "id-vault-foil", "c13", eur="3.00", eur_foil="8.00"))
    entry = client.post("/api/collection", json={"name": "Lim-Dûl's Vault", "set_code": "all"}).json()
    foil = client.patch(f"/api/collection/{entry['id']}", json={"is_foil": True}).json()
    assert (foil["unit_price"], foil["price_from_set"]) == (8.0, "c13")


def test_stylesheet_link_changes_with_the_file(client):
    import re

    from app.config import BASE_DIR

    version = int((BASE_DIR / "static" / "style.css").stat().st_mtime)
    assert re.search(rf'href="/static/style\.css\?v={version}"', client.get("/").text)
    assert client.get(f"/static/style.css?v={version}").status_code == 200


def test_collection_shows_which_decks_use_a_card(client):
    sol = client.post("/api/collection", json={"name": "Sol Ring", "set_code": "cmm", "quantity": 3}).json()
    client.post("/api/collection", json={"name": "Fire // Ice"})
    first = client.post("/api/decks", json={"name": "Ramp & Go", "decklist": "1 Sol Ring"}).json()
    second = client.post("/api/decks", json={"name": "Artifacts", "decklist": "1 Sol Ring"}).json()

    items = {i["card"]["name"]: i for i in client.get("/api/collection").json()["items"]}
    assert items["Sol Ring"]["in_decks"] == [
        {"id": first["id"], "name": "Ramp & Go", "quantity": 1},
        {"id": second["id"], "name": "Artifacts", "quantity": 1},
    ]
    assert items["Fire // Ice"]["in_decks"] == []

    page = client.get("/collection").text
    assert page.count('<details class="in-decks">') == 1  # only the card that is in a deck gets the label
    assert "in 2 decks · 1 free</summary>" in page and 'data-used="2"' in page and 'id="collection-show"' in page

    # Only the cards that are in no deck yet, or only the ones a deck uses.
    free = client.get("/api/collection", params={"in_deck": False}).json()
    assert [(i["card"]["name"], i["free"]) for i in free["items"]] == [("Fire // Ice", 1), ("Sol Ring", 1)]
    assert [i["card"]["name"] for i in client.get("/api/collection", params={"in_deck": True}).json()["items"]] == ["Sol Ring"]
    client.post("/api/decks", json={"name": "Third", "decklist": "1 Sol Ring"})
    assert [i["card"]["name"] for i in client.get("/api/collection", params={"in_deck": False}).json()["items"]] == ["Fire // Ice"]
    client.delete(f"/api/decks/{client.get('/api/decks').json()[-1]['id']}")
    assert f'<a href="/decks/{first["id"]}">Ramp &amp; Go</a>' in page and f'<a href="/decks/{second["id"]}">Artifacts</a>' in page
    assert "1 of 3 copies free" in page
    assert "Sol Ring Set CMM cmm #1 Ramp &amp; Go Artifacts" in page  # deck names are searchable too

    # Taking the card out of a deck, or deleting the deck, updates the label.
    entry_id = second["cards"][0]["entry_id"]
    client.put(f"/api/decks/{second['id']}/cards/{entry_id}/assigned", json={"assigned": False})
    client.delete(f"/api/decks/{first['id']}")
    assert '<details class="in-decks">' not in client.get("/collection").text
    assert client.get("/api/collection").json()["items"][1]["in_decks"] == []
    assert sol["in_decks"] == []  # a single entry response does not list decks

    # The card was taken out of the second deck while you own free copies: that deck still needs it.
    items = {i["card"]["name"]: i for i in client.get("/api/collection").json()["items"]}
    assert items["Sol Ring"]["wanted_by"] == [{"id": second["id"], "name": "Artifacts", "quantity": 1, "entry_id": entry_id}]
    assert items["Fire // Ice"]["wanted_by"] == []
    page = client.get("/collection").text
    assert "needed by 1 deck</summary>" in page and 'data-wanted="1"' in page and page.count('class="in-decks wanted-by"') == 1
    client.put(f"/api/decks/{second['id']}/cards/{entry_id}/assigned", json={"assigned": True})
    assert client.get("/api/collection").json()["items"][1]["wanted_by"] == []


def test_filter_collection_on_colour(client):
    for name in ("Sol Ring", "Lim-Dûl's Vault", "Fire // Ice", "Black Lotus"):
        client.post("/api/collection", json={"name": name})

    def names(**params):
        return [i["card"]["name"] for i in client.get("/api/collection", params=params).json()["items"]]

    items = {i["card"]["name"]: i["card"]["colors"] for i in client.get("/api/collection").json()["items"]}
    assert items == {"Black Lotus": None, "Fire // Ice": "UR", "Lim-Dûl's Vault": "UB", "Sol Ring": ""}  # WUBRG order
    assert names(colors="U") == ["Fire // Ice", "Lim-Dûl's Vault"]
    assert names(colors="r") == ["Fire // Ice"]
    assert names(colors="C") == ["Sol Ring"]
    assert names(colors="UB", color_match="within") == ["Lim-Dûl's Vault", "Sol Ring"]  # colourless fits any deck
    assert names(colors="UR", color_match="exact") == ["Fire // Ice"]
    assert names(colors="C", color_match="exact") == ["Sol Ring"]
    assert client.get("/api/collection", params={"colors": "U", "color_match": "nope"}).status_code == 422

    page = client.get("/collection").text
    assert 'data-colors="UB"' in page and 'data-colors=""' in page and 'data-colors="?"' in page
    assert 'id="collection-colors"' in page and "still being fetched" in page


async def test_colours_are_filled_in_for_cards_stored_earlier(session, scryfall, fake_scryfall):
    from app.jobs import fill_missing_colors
    from app.models import Card

    session.add(Card(name="Fire // Ice", set_code="mh2", set_name="Set MH2", scryfall_id="id-fire"))
    session.add(Card(name="Sol Ring", set_code="cmm", set_name="Set CMM", scryfall_id="id-sol", colors=""))
    session.commit()
    assert await fill_missing_colors(session, scryfall) == 1
    assert session.exec(select(Card).where(Card.scryfall_id == "id-fire")).one().colors == "UR"
    assert await fill_missing_colors(session, scryfall) == 0


def test_filter_decks_on_colour(client):
    client.post("/api/collection", json={"name": "Lim-Dûl's Vault"})  # colours known from the collection
    grixis = client.post("/api/decks", json={"name": "Grixis", "decklist": "1 Lim-Dûl's Vault\n1 Fire // Ice\n1 Sol Ring"}).json()
    client.post("/api/decks", json={"name": "Artifacts", "decklist": "1 Sol Ring\n1 Black Lotus"})

    def names(**params):
        return {d["name"]: d["colors"] for d in client.get("/api/decks", params=params).json()}

    assert names() == {"Grixis": "UBR", "Artifacts": ""}  # Fire // Ice was looked up on Scryfall
    assert names(colors="UBR", color_match="exact") == {"Grixis": "UBR"}
    assert names(colors="R") == {"Grixis": "UBR"}
    assert names(colors="C") == {"Artifacts": ""}
    assert names(colors="UB", color_match="within") == {"Artifacts": ""}

    page = client.get("/deck").text
    assert 'data-colors="UBR"' in page and 'id="deck-colors"' in page

    # A card added to a deck changes its colours.
    artifacts = next(d for d in client.get("/api/decks").json() if d["name"] == "Artifacts")
    client.post(f"/api/decks/{artifacts['id']}/cards", json={"name": "Fire // Ice"})
    assert names()["Artifacts"] == "UR"
    assert grixis["colors"] is None  # worked out when the decks are listed


def test_search_decks_on_name_or_card(client):
    client.post("/api/decks", json={"name": "Rabbit Tribal", "decklist": "1 Sol Ring\n1 Lim-Dûl's Vault\n10 Plains"})
    client.post("/api/decks", json={"name": "Fire Nation", "decklist": "1 Fire // Ice\n1 Sol Ring"})

    def names(q):
        return [d["name"] for d in client.get("/api/decks", params={"q": q}).json()]

    assert names("rabbit") == ["Rabbit Tribal"]  # deck name
    assert names("sol ring") == ["Rabbit Tribal", "Fire Nation"]  # a card in both decks
    assert names("lim-dul") == ["Rabbit Tribal"]  # accents do not matter
    assert names("ice") == ["Fire Nation"]
    assert names("plains") == ["Rabbit Tribal"]  # basic lands count too
    assert names("nothing like this") == []

    page = client.get("/deck", params={"q": "sol"}).text
    assert 'id="deck-list-search"' in page and 'value="sol"' in page
    assert 'data-card-names="Lim-Dûl&#39;s Vault|Sol Ring|Plains"' in page


def test_filter_wishlist_on_colour(client):
    for name in ("Sol Ring", "Lim-Dûl's Vault", "Fire // Ice"):
        client.post("/api/wishlist", json={"name": name, "target_price": 1})

    def names(**params):
        return [i["card"]["name"] for i in client.get("/api/wishlist", params=params).json()["items"]]

    assert len(names()) == 3
    assert sorted(names(colors="U")) == ["Fire // Ice", "Lim-Dûl's Vault"]
    assert names(colors="UR", color_match="exact") == ["Fire // Ice"]
    assert names(colors="C") == ["Sol Ring"]

    page = client.get("/wishlist").text
    assert 'id="wishlist-colors"' in page and 'data-colors="UB"' in page and 'class="pips"' in page
    # The shared filter is on the other pages too.
    client.post("/api/collection", json={"name": "Sol Ring"})
    assert 'id="collection-colors"' in client.get("/collection").text


def test_collection_groups_versions_and_lists_spare_copies(client):
    client.post("/api/collection", json={"name": "Sol Ring", "set_code": "cmm", "quantity": 3})
    client.post("/api/collection", json={"name": "Sol Ring", "set_code": "lea"})
    client.post("/api/collection", json={"name": "Lim-Dûl's Vault"})
    client.post("/api/collection", json={"name": "Fire // Ice"})
    client.post("/api/decks", json={"name": "Ramp", "decklist": "1 Sol Ring\n1 Fire // Ice"})

    page = client.get("/collection").text
    # One summary line for the two Sol Ring versions; cards with a single version have none.
    assert page.count('class="group-row"') == 1 and "2 versions" in page
    assert '<tbody class="card-group" data-name="Sol Ring"' in page
    assert 'id="collection-group"' in page and 'option value="spare"' in page

    items = client.get("/api/collection", params={"spare": True}).json()["items"]
    spare = sorted((i["card"]["name"], i["card"]["set_code"], i["spare"]) for i in items)
    # The deck uses one Sol Ring (the cheapest) and Fire // Ice; everything else is spare.
    assert spare == [("Lim-Dûl's Vault", "all", 1), ("Sol Ring", "cmm", 2), ("Sol Ring", "lea", 1)]


def test_mark_a_deck_as_using_tokens(client):
    with_tokens = client.post("/api/decks", json={"name": "Rabbits", "decklist": "1 Sol Ring\n3 Treasure Token"}).json()
    plain = client.post("/api/decks", json={"name": "Plain", "decklist": "1 Sol Ring"}).json()

    def tokens():
        return {d["name"]: (d["uses_tokens"], d["token_count"]) for d in client.get("/api/decks").json()}

    # A deck whose list has tokens uses them, until you say otherwise.
    assert tokens() == {"Rabbits": (True, 3), "Plain": (False, 0)}
    client.put(f"/api/decks/{plain['id']}/tokens", json={"uses_tokens": True})
    client.put(f"/api/decks/{with_tokens['id']}/tokens", json={"uses_tokens": False})
    assert tokens() == {"Rabbits": (False, 3), "Plain": (True, 0)}
    client.put(f"/api/decks/{with_tokens['id']}/tokens", json={"uses_tokens": None})
    assert tokens()["Rabbits"] == (True, 3)

    # The "Tokens owned" box on the deck page is saved with the other changes.
    client.post(f"/decks/{plain['id']}/save", data={"tokens_shown": "1"})  # unticked
    assert tokens()["Plain"] == (False, 0)
    client.post(f"/decks/{plain['id']}/save", data={"tokens_shown": "1", "uses_tokens": "on"})
    assert tokens()["Plain"] == (True, 0)
    page = client.get(f"/decks/{plain['id']}").text
    assert 'name="uses_tokens" form="deck-form" checked' in page

    overview = client.get("/deck").text
    assert overview.count("✓ tokens owned</span>") == 2 and 'id="deck-tokens-filter"' in overview
    assert "Tokens owned</label>" in page and "Only decks whose tokens I own" in overview

    # Putting a token on the list does not tick the box: listing a token is not owning it.
    client.post(f"/decks/{plain['id']}/save", data={"tokens_shown": "1"})
    client.post(f"/decks/{plain['id']}/tokens", data={"name": "Treasure", "quantity": 2})
    assert tokens()["Plain"] == (False, 2)
