"""Typing ahead, the commanders of opponents, the weakest cards of a deck and the live table."""

from app.models import Deck
from app.services.games import log_game, log_stats


# --- typing ahead -------------------------------------------------------------------------------


def test_autocomplete(client):
    assert client.get("/api/autocomplete", params={"q": "sol"}).json() == {"names": ["Sol Ring"]}
    assert client.get("/api/autocomplete", params={"q": "s"}).json() == {"names": []}  # too short
    # Tokens only when asked for.
    assert client.get("/api/autocomplete", params={"q": "treas"}).json() == {"names": []}
    assert client.get("/api/autocomplete", params={"q": "treas", "kind": "token"}).json() == {"names": ["Treasure"]}
    # Commanders: only cards that can be one.
    assert client.get("/api/autocomplete", params={"q": "marw", "kind": "commander"}).json() == {"names": ["Marwyn, the Nurturer"]}
    assert client.get("/api/autocomplete", params={"q": "vault", "kind": "commander"}).json() == {"names": []}
    page = client.get("/collection").text
    assert 'data-autocomplete="card"' in page and "attachAutocomplete" in page


# --- the commanders of opponents ------------------------------------------------------------------


def test_opponent_commanders(client):
    deck = client.post("/api/decks", json={"name": "Ramp", "decklist": "1 Sol Ring"}).json()
    game = client.post("/api/games", json={
        "deck_id": deck["id"], "result": "loss", "opponents": ["Sam", "Robin"], "winner": "Sam",
        "opponent_commanders": {"sam": " Atraxa,  Praetors' Voice ", "Robin": "", "Alex": "Edgar Markov"},
    }).json()
    # Matched to the names of the opponents; empty ones and players not at the table are left out.
    assert game["opponent_commanders"] == '{"Sam": "Atraxa, Praetors\' Voice"}'

    # The page form sends them as JSON.
    client.post("/games", data={"deck_id": deck["id"], "result": "win", "opponents": "Sam",
                                "opponent_commanders": '{"Sam": "Atraxa, Praetors\' Voice"}'})
    overview = client.get("/api/games/overview").json()
    assert overview["commanders"] == [{"commander": "Atraxa, Praetors' Voice", "played": 2, "your_wins": 1, "their_wins": 1}]
    assert next(o for o in overview["opponents"] if o["name"] == "Sam")["commanders"] == ["Atraxa, Praetors' Voice"]

    page = client.get("/games").text
    assert "Commanders you played against" in page and "Sam <span class=\"muted\">(Atraxa, Praetors&#39; Voice)</span>" in page
    assert '"Sam": "Atraxa, Praetors\\u0027 Voice"' in page  # filled in for the next game (as JSON)


def test_commander_stats(session):
    deck = Deck(name="Elves", colors="G")
    session.add(deck)
    session.flush()
    log_game(session, deck, "loss", opponents="Sam, Robin", winner="Sam", opponent_commanders={"Sam": "Atraxa", "Robin": "Edgar"})
    log_game(session, deck, "loss", opponents="Sam", winner="Sam", opponent_commanders={"Sam": "Atraxa"})
    log_game(session, deck, "win", opponents="Robin", opponent_commanders={"Robin": "Edgar"})
    stats = log_stats(session)
    assert [(t.label, t.played, t.wins, t.their_wins) for t in stats.commanders] == [("Atraxa", 2, 0, 2), ("Edgar", 2, 1, 0)]


# --- the weakest cards ------------------------------------------------------------------------------


def test_weakest_cards(client):
    client.post("/api/collection", json={"name": "Lim-Dûl's Vault"})
    deck = client.post("/api/decks", json={
        "name": "Marwyn, the Nurturer - Elves",
        "decklist": "1 Marwyn, the Nurturer\n1 Sol Ring\n1 Pitiless Plunderer\n1 Fire // Ice\n1 Time Warp\n5 Forest",
    }).json()
    client.get("/api/decks")  # works out the commander
    weak = {w["name"]: w for w in client.get(f"/api/decks/{deck['id']}/weakest").json()}
    # Not on EDHREC's list at all: played even less than the least played card on it; Sol Ring (95%) and
    # Pitiless Plunderer (12%) are not weak, the commander and basic lands do not count.
    assert list(weak) == ["Fire // Ice", "Time Warp"]
    assert weak["Fire // Ice"]["inclusion"] is None
    # Lim-Dûl's Vault (30%, a free copy) is an instant too: the swap for Fire // Ice.
    assert weak["Fire // Ice"]["swap"] == {"name": "Lim-Dûl's Vault", "inclusion": 30, "free": 1}
    assert weak["Time Warp"]["swap"] is None
    page = client.get(f"/decks/{deck['id']}").text
    assert "Weakest cards" in page and "under 12%" in page


# --- the live table --------------------------------------------------------------------------------


def test_table_page(client):
    client.post("/api/decks", json={"name": "Ramp", "decklist": "1 Sol Ring"})
    page = client.get("/table").text
    assert "New game" in page and "Start the game" in page and 'href="/table"' in page and "wakeLock" in page
