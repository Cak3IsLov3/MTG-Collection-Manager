"""Exports, token lists, mana curve and card types, proxies and the weekly summary."""

import csv
import io

from app.jobs import send_weekly_summary
from app.services.decks import parse_archidekt_deck, save_deck, select_tracked
from tests.test_csv_import import MOXFIELD


def test_export_collection_and_wishlist(client):
    files = {"file": ("moxfield.csv", MOXFIELD, "text/csv")}
    client.post("/api/collection/import", files=files)
    exported = client.get("/collection/export.csv")
    assert exported.headers["content-type"].startswith("text/csv")
    rows = list(csv.DictReader(io.StringIO(exported.text)))
    assert sorted((r["Count"], r["Name"], r["Edition"], r["Foil"]) for r in rows) == [
        ("1", "Fire // Ice", "mh2", "foil"), ("3", "Sol Ring", "cmm", ""),
    ]
    # The export can be imported again: replacing with it changes nothing.
    again = client.post("/api/collection/import", params={"replace": True},
                        files={"file": ("export.csv", exported.text, "text/csv")}).json()
    assert (again["imported_cards"], again["new_copies"]) == (4, 0)

    client.post("/api/wishlist", json={"name": "Lim-Dûl's Vault", "target_price": 10})
    wish = list(csv.DictReader(io.StringIO(client.get("/wishlist/export.csv").text)))
    assert (wish[0]["Name"], wish[0]["Target price (EUR)"], wish[0]["Current price (EUR)"]) == ("Lim-Dûl's Vault", "10.00", "12.34")


def test_export_deck_list(client):
    deck = client.post("/api/decks", json={
        "name": "Marwyn, the Nurturer - Elves",
        "decklist": "1 Sol Ring (cmm) 1\n1 Marwyn, the Nurturer\n2 Fire // Ice (mh2) 1 *F*\n10 Forest\n3 Treasure Token",
    }).json()
    client.get("/api/decks")  # works out the commander
    response = client.get(f"/decks/{deck['id']}/export.txt")
    assert "attachment" in response.headers["content-disposition"]
    assert response.text.splitlines() == [
        "// Marwyn, the Nurturer - Elves",
        "// Commander", "1 Marwyn, the Nurturer",
        "// Deck", "2 Fire // Ice (MH2) 1 *F*", "1 Sol Ring (CMM) 1", "10 Forest",
        "// Tokens (not part of the deck)", "3 Treasure Token",
    ]


def test_token_list(client, session):
    archidekt = {
        "categories": [{"name": "Tokens & Extras", "includedInDeck": False}],
        "cards": [
            {"quantity": 1, "categories": ["Ramp"], "card": {"oracleCard": {"name": "Sol Ring", "types": ["Artifact"]}}},
            {"quantity": 3, "categories": ["Tokens & Extras"], "card": {"uid": "id-treasure",
             "oracleCard": {"name": "Treasure", "layout": "token", "types": ["Token", "Artifact"]}}},
            {"quantity": 2, "categories": ["Tokens & Extras"], "card": {"oracleCard": {"name": "Treasure", "layout": "token"}}},
        ],
    }
    _, cards = parse_archidekt_deck(archidekt)
    tracked, left_out = select_tracked(cards)
    deck = save_deck(session, "Treasures", tracked, left_out)
    session.commit()

    page = client.get(f"/decks/{deck.id}").text  # looks up the token picture
    assert 'value="5" required' in page and 'aria-label="How many Treasure tokens"' in page and "https://img.example/id-treasure.jpg" in page and "5 to bring" in page

    client.post(f"/decks/{deck.id}/tokens", data={"name": "Saproling Token", "quantity": "2"})
    detail = client.get(f"/api/decks/{deck.id}").json()
    assert detail["token_count"] == 7 and detail["uses_tokens"]
    token_id = next(t.id for t in __import__("app.services.decks", fromlist=["deck_tokens"]).deck_tokens(session, deck) if t.name == "Saproling")
    # A typo in the quantity is fixed in place.
    response = client.post(f"/decks/{deck.id}/tokens/{token_id}/quantity", data={"quantity": "4"})
    assert "Saproling: 4 now" in response.text
    assert client.get(f"/api/decks/{deck.id}").json()["token_count"] == 9
    assert "at least 1" in client.post(f"/decks/{deck.id}/tokens/{token_id}/quantity", data={"quantity": "0"}).text
    client.post(f"/decks/{deck.id}/tokens/{token_id}/delete")
    assert client.get(f"/api/decks/{deck.id}").json()["token_count"] == 5

    # Saving a checked deck keeps the token names too.
    checked = client.post("/deck", data={"decklist": "1 Sol Ring\n4 Treasure Token", "action": "check"}).text
    assert 'name="token:Treasure Token" value="4"' in checked


def test_mana_curve_and_card_types(client):
    deck = client.post("/api/decks", json={
        "name": "Mix", "decklist": "1 Sol Ring\n2 Lim-Dûl's Vault\n1 Fire // Ice\n1 Marwyn, the Nurturer\n20 Forest\n1 Nonsense Card",
    }).json()
    page = client.get(f"/decks/{deck['id']}").text
    assert 'class="curve"' in page and 'class="colour-ring"' in page
    assert "<td>Creature</td><td class=\"num\">1</td>" in page and "<td>Instant</td><td class=\"num\">3</td>" in page
    assert "<td>Land</td><td class=\"num\">20</td>" in page and "<td>Artifact</td><td class=\"num\">1</td>" in page
    assert "Ramp 2" in page and "Card draw 1" in page  # Sol Ring and Marwyn add mana; Ice draws
    assert "average mana value 2.4" in page  # (1 + 2 + 2 + 4 + 3) / 5
    assert "1 card not counted" in page
    for bucket, cards in (("1", 1), ("2", 2), ("3", 1), ("4", 1)):
        assert f'aria-label="{cards} card{"s" if cards != 1 else ""} with mana value {bucket}"' in page


def test_proxies(client):
    client.post("/api/collection", json={"name": "Sol Ring"})
    deck = client.post("/api/decks", json={"name": "Proxies", "decklist": "1 Sol Ring\n2 Lim-Dûl's Vault"}).json()
    missing = client.get(f"/decks/{deck['id']}/proxies").text
    assert missing.count('class="proxy"') == 2 and "https://img.example/id-vault.jpg" in missing
    everything = client.get(f"/decks/{deck['id']}/proxies", params={"all": 1}).text
    assert everything.count('class="proxy"') == 3


class FakeNotifier:
    def __init__(self):
        self.sent = []

    async def send_weekly_summary(self, lines, period, title="Your week in Magic"):
        self.sent.append((lines, period))
        self.title = title
        return True


async def test_weekly_summary(client, session):
    client.post("/api/collection", json={"name": "Lim-Dûl's Vault", "quantity": 2})
    deck = client.post("/api/decks", json={"name": "Vault", "decklist": "1 Lim-Dûl's Vault"}).json()
    client.post("/api/games", json={"deck_id": deck["id"], "result": "win"})
    client.post("/api/games", json={"deck_id": deck["id"], "result": "loss"})

    weekly = client.get("/api/summary/weekly").json()
    assert (weekly["games"], weekly["wins"], weekly["new_copies"]) == (2, 1, 2)
    assert weekly["collection_value"] == 24.68
    assert "**Games** 2 played, 1 won · Vault 2×" in weekly["lines"]

    notifier = FakeNotifier()
    assert await send_weekly_summary(session, notifier) is True
    lines, period = notifier.sent[0]
    assert lines[0].startswith("**Collection** €24.68") and " – " in period
    page = client.get("/").text
    assert "This week" in page and "2 games played, 1 won" in page


def test_choose_which_token(client, session):
    from app.services.decks import deck_tokens

    deck = client.post("/api/decks", json={"name": "Angels", "decklist": "1 Sol Ring"}).json()
    client.post(f"/decks/{deck['id']}/tokens", data={"name": "Angel", "quantity": "2"})
    versions = client.get("/api/tokens", params={"name": "Angel"}).json()
    assert [(v["scryfall_id"], v["description"]) for v in versions] == [
        ("id-angel-44", "4/4 · W · Flying"), ("id-angel-11", "1/1 · W · Flying, lifelink"),
    ]
    saved = session.get(__import__("app.models", fromlist=["Deck"]).Deck, deck["id"])
    [token] = deck_tokens(session, saved)
    page = client.post(f"/decks/{deck['id']}/tokens/{token.id}/version", data={"scryfall_id": "id-angel-11"})
    assert "Changed the Angel token" in page.text and "https://img.example/id-angel-11.jpg" in page.text

    # Another Angel is its own line, to set to the other version.
    client.post(f"/decks/{deck['id']}/tokens", data={"name": "Angel", "quantity": "1"})
    session.expire_all()
    assert sorted((t.quantity, t.scryfall_id) for t in deck_tokens(session, saved)) == [(1, None), (2, "id-angel-11")]
    assert client.get(f"/api/decks/{deck['id']}").json()["token_count"] == 3


def test_tokens_in_alphabetical_order_with_emblems(client):
    deck = client.post("/api/decks", json={"name": "Tokens", "decklist": "1 Sol Ring"}).json()
    for name in ("Treasure", "cat beast", "Elspeth, Sun's Champion Emblem", "Morph"):
        client.post(f"/decks/{deck['id']}/tokens", data={"name": name, "quantity": "1"})
    page = client.get(f"/decks/{deck['id']}").text
    # Scryfall's spelling is taken over, emblems are found too, and the list is in alphabetical order.
    assert page.index("How many Cat Beast tokens") < page.index("How many Elspeth, Sun&#39;s Champion Emblem tokens") < page.index("How many Treasure tokens")
    assert "https://img.example/id-elspeth-emblem.jpg" in page and "https://img.example/id-cat-beast.jpg" in page
    assert "https://img.example/id-morph.jpg" in page  # a helper card, not a "Token" by type
