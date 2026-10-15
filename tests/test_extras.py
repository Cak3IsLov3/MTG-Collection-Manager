"""Commander pictures, deck value over time, price rise alerts and the game log."""

import datetime as dt

from sqlmodel import select

from app.jobs import check_price_rises
from app.models import Card, Deck, PriceRiseAlert, PriceSnapshot
from app.services.decks import parse_archidekt_deck, parse_decklist, save_deck, select_tracked
from app.services.pricing import SCRYFALL, record_snapshot

TODAY = dt.date.today()


# --- commander ----------------------------------------------------------------------


def test_commander_comes_from_the_deck_list(session):
    archidekt = {
        "categories": [{"name": "Commander", "includedInDeck": True, "isPremier": True}],
        "cards": [
            {"quantity": 1, "categories": ["Commander"], "card": {"oracleCard": {"name": "Lim-Dûl's Vault", "types": ["Instant"]}}},
            {"quantity": 1, "categories": ["Ramp"], "card": {"oracleCard": {"name": "Sol Ring", "types": ["Artifact"]}}},
        ],
    }
    _, cards = parse_archidekt_deck(archidekt)
    deck = save_deck(session, "Vault", select_tracked(cards)[0])
    assert deck.commander == "Lim-Dûl's Vault"

    text = save_deck(session, "Text", parse_decklist("1x Fire // Ice (mh2) 1 [Commander{top}]\n1 Sol Ring [Ramp]"))
    assert text.commander == "Fire // Ice"


def test_commander_picture_and_choice(client):
    # Named after its commander: the app works that out by itself...
    deck = client.post("/api/decks", json={"name": "Marwyn, the Nurturer - Elves", "decklist": "1 Marwyn, the Nurturer\n1 Sol Ring\n1 Lim-Dûl's Vault"}).json()
    # ...but only when that card can be a commander: Lim-Dûl's Vault is an instant.
    spell = client.post("/api/decks", json={"name": "Lim-Dûl's Vault - Control", "decklist": "1 Lim-Dûl's Vault"}).json()
    listed = {d["name"]: d for d in client.get("/api/decks").json()}
    assert listed["Marwyn, the Nurturer - Elves"]["commander"] == "Marwyn, the Nurturer"
    assert listed["Marwyn, the Nurturer - Elves"]["commander_image"] == "https://img.example/id-marwyn.jpg"
    assert (listed["Lim-Dûl's Vault - Control"]["commander"], listed["Lim-Dûl's Vault - Control"]["commander_image"]) == (None, None)
    assert spell["id"] != deck["id"]

    chosen = client.put(f"/api/decks/{deck['id']}/commander", json={"commander": "Sol Ring"}).json()
    assert chosen["commander"] == "Sol Ring" and chosen["commander_image"].startswith("https://img.example/id-sol")
    assert client.put(f"/api/decks/{deck['id']}/commander", json={"commander": "Black Lotus"}).status_code == 400
    assert client.put(f"/api/decks/{deck['id']}/commander", json={"commander": None}).json()["commander"] is None

    # The deck page form saves it along with the other changes.
    client.post(f"/decks/{deck['id']}/save", data={"commander": "Marwyn, the Nurturer"})
    page = client.get(f"/decks/{deck['id']}").text
    assert 'class="commander-art"' in page and '<option value="Marwyn, the Nurturer" selected>' in page
    assert 'class="thumb commander-thumb"' in client.get("/deck").text


# --- value over time ------------------------------------------------------------------


def test_deck_value_over_time(client, session):
    client.post("/api/collection", json={"name": "Sol Ring", "set_code": "cmm", "quantity": 2})
    deck = client.post("/api/decks", json={"name": "Ramp", "decklist": "2 Sol Ring"}).json()
    sol = session.exec(select(Card).where(Card.scryfall_id == "id-sol")).one()
    record_snapshot(session, sol.id, SCRYFALL, 1.0, day=TODAY - dt.timedelta(days=2))
    record_snapshot(session, sol.id, SCRYFALL, 1.25, day=TODAY - dt.timedelta(days=1))
    session.commit()

    history = client.get(f"/api/decks/{deck['id']}/value-history").json()
    assert history["values"] == [2.0, 2.5, 3.0]  # two copies; today's price is 1.50
    assert history["labels"][-1] == TODAY.isoformat()
    page = client.get(f"/decks/{deck['id']}").text
    assert 'id="deck-value-chart"' in page and "+1.00 (+50.0%)" in page


# --- price rises ------------------------------------------------------------------------


class FakeNotifier:
    configured = True

    def __init__(self):
        self.sent = []

    async def send_price_rises(self, lines, days):
        self.sent.append((lines, days))
        return True


async def test_price_rise_alerts(client, session):
    client.post("/api/collection", json={"name": "Lim-Dûl's Vault", "quantity": 2})  # €12.34 today
    client.post("/api/collection", json={"name": "Sol Ring", "set_code": "cmm"})  # €1.50 today
    client.post("/api/decks", json={"name": "Vault", "decklist": "1 Lim-Dûl's Vault"})
    vault = session.exec(select(Card).where(Card.scryfall_id == "id-vault")).one()
    sol = session.exec(select(Card).where(Card.scryfall_id == "id-sol")).one()
    week_ago = TODAY - dt.timedelta(days=6)
    record_snapshot(session, vault.id, SCRYFALL, 8.0, day=week_ago)  # +54%
    record_snapshot(session, sol.id, SCRYFALL, 1.40, day=week_ago)  # +7%, too little
    session.commit()

    notifier = FakeNotifier()
    alerts = await check_price_rises(session, notifier, min_pct=25, min_eur=1, days=7)
    assert [(a.card_id, a.old_price, a.new_price, a.notified) for a in alerts] == [(vault.id, 8.0, 12.34, True)]
    lines, days = notifier.sent[0]
    assert days == 7 and "Lim-Dûl's Vault" in lines[0] and "you have 2, 1 spare" in lines[0]

    # Not again the next day, and nothing at all when switched off.
    assert await check_price_rises(session, notifier, min_pct=25, min_eur=1, days=7) == []
    assert await check_price_rises(session, notifier, min_pct=0, min_eur=1, days=7) == []
    assert len(notifier.sent) == 1
    assert "data-tab=\"rises\"" in client.get("/").text and "sent to Discord" in client.get("/").text


async def test_price_rise_without_webhook_is_only_logged(client, session):
    class Unconfigured(FakeNotifier):
        configured = False

    client.post("/api/collection", json={"name": "Lim-Dûl's Vault"})
    vault = session.exec(select(Card).where(Card.scryfall_id == "id-vault")).one()
    record_snapshot(session, vault.id, SCRYFALL, 5.0, day=TODAY - dt.timedelta(days=3))
    session.commit()
    notifier = Unconfigured()
    [alert] = await check_price_rises(session, notifier, min_pct=25, min_eur=1, days=7)
    assert alert.notified is False and notifier.sent == []
    assert session.exec(select(PriceRiseAlert)).one().new_price == 12.34


# --- game log ---------------------------------------------------------------------------


def test_game_log(client):
    ramp = client.post("/api/decks", json={"name": "Ramp", "decklist": "1 Sol Ring"}).json()
    vault = client.post("/api/decks", json={"name": "Vault", "decklist": "1 Lim-Dûl's Vault"}).json()

    yesterday = (TODAY - dt.timedelta(days=1)).isoformat()
    client.post("/api/games", json={"deck_id": ramp["id"], "result": "win", "played_on": yesterday, "players": 4})
    client.post("/api/games", json={"deck_id": ramp["id"], "result": "loss"})
    assert client.post("/api/games", json={"deck_id": ramp["id"], "result": "maybe"}).status_code == 400
    future = (TODAY + dt.timedelta(days=1)).isoformat()
    assert client.post("/api/games", json={"deck_id": ramp["id"], "result": "win", "played_on": future}).status_code == 400

    [stats] = client.get("/api/games/stats").json()
    assert (stats["deck_name"], stats["played"], stats["wins"], stats["win_rate"]) == ("Ramp", 2, 1, 50)
    assert stats["last_played"] == TODAY.isoformat()

    # The pages: log from the deck page and get sent back there.
    response = client.post("/games", data={"deck_id": vault["id"], "result": "draw", "back": f"/decks/{vault['id']}"})
    assert response.url.path == f"/decks/{vault['id']}" and "Logged: draw with Vault" in response.text
    games_page = client.get("/games").text
    assert games_page.count('class="badge result-') == 3 and "50%" in games_page
    overview = client.get("/deck").text
    assert '"days_since_played": 0' in overview and 'id="random-fresh"' in overview and "2× " in overview

    # A deleted deck keeps its games in the log.
    client.delete(f"/api/decks/{vault['id']}")
    games = client.get("/api/games").json()
    assert [(g["deck_name"], g["deck_id"]) for g in games if g["result"] == "draw"] == [("Vault", None)]
    assert "(deleted)" in client.get("/games").text
    game_id = games[0]["id"]
    assert client.delete(f"/api/games/{game_id}").status_code == 204
    assert len(client.get("/api/games").json()) == 2
