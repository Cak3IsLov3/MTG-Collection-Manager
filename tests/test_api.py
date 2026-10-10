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
        "format": "moxfield", "imported_rows": 2, "imported_cards": 4, "not_found": [], "errors": [],
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
