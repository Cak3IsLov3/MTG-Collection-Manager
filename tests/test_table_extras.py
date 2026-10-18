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
    assert "Weakest cards" in page and "under 12%" in page and 'id="weakest-mine"' in page
    assert '<tr data-swap="1">' in page and '<tr data-swap="0">' in page


# --- the live table --------------------------------------------------------------------------------


def test_table_page(client):
    client.post("/api/decks", json={"name": "Ramp", "decklist": "1 Sol Ring"})
    page = client.get("/table").text
    assert "New game" in page and "Start the game" in page and 'href="/table"' in page and "wakeLock" in page


# --- searching everything, game nights, the coloured curve --------------------------------------------


def test_search_everything(client):
    client.post("/api/collection", json={"name": "Sol Ring", "set_code": "cmm", "quantity": 2})
    client.post("/api/collection", json={"name": "Sol Ring", "set_code": "lea"})
    client.post("/api/decks", json={"name": "Artifacts", "decklist": "1 Sol Ring\n1 Lim-Dûl's Vault"})
    client.post("/api/decks", json={"name": "Solar Flare", "decklist": "1 Fire // Ice"})
    client.post("/api/wishlist", json={"name": "Lim-Dûl's Vault", "target_price": 5})
    found = client.get("/api/search", params={"q": "sol"}).json()
    assert [(c["name"], c["copies"], c["versions"]) for c in found["cards"]] == [("Sol Ring", 3, 2)]
    # A deck by its name first, then decks that have a matching card.
    assert [(d["name"], d["cards"]) for d in found["decks"]] == [("Solar Flare", []), ("Artifacts", ["Sol Ring"])]
    vault = client.get("/api/search", params={"q": "lim dul"}).json()  # accents and word order do not matter
    assert [w["name"] for w in vault["wishlist"]] == ["Lim-Dûl's Vault"] and vault["cards"] == []
    assert client.get("/api/search", params={"q": "s"}).json()["cards"] == []
    assert 'id="global-search"' in client.get("/").text


async def test_game_nights(client, session):
    import datetime as dt

    from app.jobs import send_game_night

    deck = client.post("/api/decks", json={"name": "Ramp", "decklist": "1 Sol Ring"}).json()
    day = (dt.date.today() - dt.timedelta(days=1)).isoformat()
    client.post("/api/games", json={"deck_id": deck["id"], "result": "win", "played_on": day, "opponents": ["Sam"],
                                    "opponent_commanders": {"Sam": "Atraxa"}})
    client.post("/api/games", json={"deck_id": deck["id"], "result": "loss", "played_on": day, "opponents": ["Sam", "Robin"],
                                    "winner": "Sam", "notes": "Close one"})
    [night] = client.get("/api/games/nights").json()
    assert (night["games"], night["wins"], night["winners"], night["decks"]) == (2, 1, {"You": 1, "Sam": 1}, {"Ramp": 2})
    assert night["players"] == ["Sam", "Robin"] and night["commanders"] == {"Atraxa": 1} and night["sent"] is False

    class Notifier:
        configured = True
        sent = []

        async def send_game_night(self, lines, when):
            self.sent.append((lines, when))
            return True

    notifier = Notifier()
    assert await send_game_night(session, notifier, dt.date.fromisoformat(day))
    lines, when = notifier.sent[0]
    assert when == day and lines[0] == "**2 games**, you won 1" and "**Wins** You 1× · Sam 1×" in lines
    assert client.get("/api/games/nights").json()[0]["sent"] is True
    page = client.get("/games").text
    assert "Game nights" in page and "✓ Sent to Discord" in page


def test_curve_in_colours(client):
    deck = client.post("/api/decks", json={"name": "Mix", "decklist": "1 Sol Ring\n1 Lim-Dûl's Vault\n1 Marwyn, the Nurturer"}).json()
    page = client.get(f"/decks/{deck['id']}").text
    # Sol Ring colourless (1), Lim-Dûl's Vault blue-black (2), Marwyn green (3).
    assert '"C": [0, 1, 0, 0, 0, 0, 0, 0]' in page and '"M": [0, 0, 1, 0, 0, 0, 0, 0]' in page and '"G": [0, 0, 0, 1, 0, 0, 0, 0]' in page
    assert 'class="deck-hero' in page and 'data-card-name="Sol Ring"' in page


def test_rarity_and_bracket_colours(client):
    client.post("/api/collection", json={"name": "Sol Ring", "set_code": "cmm"})
    page = client.get("/collection").text
    assert 'class="set-mark rarity-uncommon"' in page and '<span class="rarity-uncommon">CMM</span>' in page
    client.post("/api/decks", json={"name": "Ramp", "decklist": "1 Sol Ring", "bracket": 4})
    assert 'class="badge bracket bracket-4"' in client.get("/deck").text


def test_commander_picture_follows_the_printing(client):
    # You own Sol Ring in two printings; the deck list names the CMM #410 one, and that is its picture.
    client.post("/api/collection", json={"name": "Sol Ring", "set_code": "cmm"})
    client.post("/api/collection", json={"set_code": "cmm", "collector_number": "410"})
    deck = client.post("/api/decks", json={"name": "Ring", "decklist": "1 Sol Ring (cmm) 410"}).json()
    client.put(f"/api/decks/{deck['id']}/commander", json={"commander": "Sol Ring"})
    page = client.get(f"/decks/{deck['id']}").text
    assert 'class="commander-art" src="https://img.example/id-sol-cmm-art.jpg"' in page
