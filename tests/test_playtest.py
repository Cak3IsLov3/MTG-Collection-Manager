"""The playtest table: a hand only you see, a battlefield on the stream, and the page to play it on."""

import httpx
import pytest
from fastapi.testclient import TestClient

from app.stream.server import create_app

IMG = "https://cards.scryfall.io/normal/front/a/b/{}.jpg"


@pytest.fixture
def state_path(tmp_path):
    return tmp_path / "state.json"


@pytest.fixture
def client(state_path):
    with TestClient(create_app(state_path)) as c:
        yield c


def deck(lands=10):
    return {"name": "Test deck", "commander": {"name": "Krenko", "image_url": IMG.format("krenko"), "type_line": "Legendary Creature"},
            "cards": [{"name": "Forest", "image_url": IMG.format("forest"), "type_line": "Basic Land — Forest", "count": lands},
                      {"name": "Llanowar Elves", "image_url": IMG.format("elves"), "type_line": "Creature — Elf", "count": 4},
                      {"name": "Sol Ring", "image_url": IMG.format("sol"), "type_line": "Artifact", "count": 1},
                      {"name": "Rhystic Study", "image_url": IMG.format("rs"), "type_line": "Enchantment", "count": 2}]}


def state(client):
    return client.get("/api/state").json()["play"]


def private(client, query=""):
    return client.get(f"/api/play/private{query}").json()


def test_a_hand_is_dealt_and_hidden_from_the_overlay(client):
    assert state(client)["loaded"] is False
    assert client.post("/api/play/deck", json=deck()).status_code == 201
    public = state(client)
    assert public["loaded"] and public["hand"] == 7 and public["library"] == 10
    assert [c["name"] for c in public["command"]] == ["Krenko"]
    assert not any(key in public for key in ("hand_cards", "library_cards", "top")) and public["life"] == 40
    assert len(private(client)["hand"]) == 7 and private(client)["opening"] is True
    assert len(private(client, "?peek=3")["top"]) == 3 and len(private(client, "?bottom=2")["bottom"]) == 2
    client.post("/api/play/keep")
    assert private(client)["opening"] is False


def test_the_page_and_the_overlay_hook_are_served(client):
    page = client.get("/playtest")
    assert page.status_code == 200 and "Playtest" in page.text and page.headers["cache-control"] == "no-store"
    assert "id=\"play\"" in client.get("/overlay").text
    assert "playTabBtn" in client.get("/control").text


def test_cards_move_between_zones_and_the_library(client):
    client.post("/api/play/deck", json=deck())
    first = private(client)["hand"][0]["uid"]
    client.post("/api/play/move", json={"uid": first, "to": "battlefield"})
    assert state(client)["hand"] == 6 and [c["uid"] for c in state(client)["battlefield"]] == [first]
    # only a card on the battlefield taps; leaving it loses what it had
    client.patch(f"/api/play/cards/{first}", json={"tapped": True, "counter": {"type": "+1/+1", "delta": 2}})
    card = state(client)["battlefield"][0]
    assert card["tapped"] and card["counters"] == [{"t": "+1/+1", "n": 2}]
    client.post("/api/play/move", json={"uid": first, "to": "graveyard"})
    gone = state(client)["graveyard"][0]
    assert gone["tapped"] is False and gone["counters"] == []
    assert client.patch(f"/api/play/cards/{first}", json={"tapped": True}).status_code == 404
    assert client.post("/api/play/move", json={"uid": "nope", "to": "hand"}).status_code == 404
    assert client.post("/api/play/move", json={"uid": first, "to": "sideways"}).status_code == 422
    # a place in the library: the top, the bottom, a number, and shuffled in
    for to, extra in (("library-top", {}), ("library-bottom", {}), ("library", {"position": 3})):
        client.post("/api/play/move", json={"uid": first, "to": to, **extra})
    assert private(client, "?peek=3")["top"][2]["uid"] == first
    client.post("/api/play/move", json={"uid": first, "to": "library-bottom"})
    assert private(client, "?bottom=1")["bottom"][0]["uid"] == first
    assert client.post("/api/play/move", json={"uid": first, "to": "library", "position": 0}).status_code == 422
    client.post("/api/play/move", json={"uid": first, "to": "library-shuffle"})
    assert state(client)["library"] == 11


def test_library_actions(client):
    client.post("/api/play/deck", json=deck())
    start = state(client)["library"]
    top = private(client, "?peek=2")["top"]
    client.post("/api/play/mill", json={"count": 2})
    assert [c["uid"] for c in state(client)["graveyard"]] == [c["uid"] for c in top] and state(client)["library"] == start - 2
    last = private(client, "?bottom=1")["bottom"][0]["uid"]
    client.post("/api/play/mill", json={"count": 1, "to": "exile", "bottom": True})
    assert state(client)["exile"][0]["uid"] == last
    assert client.post("/api/play/mill", json={"count": 1, "to": "nowhere"}).status_code == 422
    bottom = private(client, "?bottom=1")["bottom"][0]["uid"]
    client.post("/api/play/bottom-to-top")
    assert private(client, "?peek=1")["top"][0]["uid"] == bottom
    client.post("/api/play/top-to-bottom")
    assert private(client, "?bottom=1")["bottom"][0]["uid"] == bottom
    hand = state(client)["hand"]
    client.post("/api/play/draw-random")
    assert state(client)["hand"] == hand + 1
    # searching: the whole library by name, a card to the hand or the battlefield, then a shuffle
    listed = private(client, "?library=true")["library"]
    names = [c["name"].lower() for c in listed]
    assert names == sorted(names) and private(client)["library"] == []
    client.post("/api/play/tutor", json={"uid": listed[0]["uid"], "to": "battlefield"})
    assert listed[0]["uid"] in [c["uid"] for c in state(client)["battlefield"]]
    assert client.post("/api/play/tutor", json={"uid": listed[0]["uid"]}).status_code == 404
    # revealing the top card shows it to the others; any change of the library hides it
    client.post("/api/play/reveal?show=true")
    assert state(client)["revealed"]["uid"] == private(client, "?peek=1")["top"][0]["uid"]
    client.post("/api/play/draw")
    assert state(client)["revealed"] is None


def test_card_states_for_the_others_and_for_you(client):
    client.post("/api/play/deck", json=deck())
    a, b = (c["uid"] for c in private(client)["hand"][:2])
    client.post("/api/play/move", json={"uid": a, "to": "battlefield"})
    client.post("/api/play/move", json={"uid": b, "to": "battlefield"})
    client.patch(f"/api/play/cards/{a}", json={"facedown": True, "phased": True, "rot": True, "power_delta": 2, "toughness_delta": -1})
    card = next(c for c in state(client)["battlefield"] if c["uid"] == a)
    assert card["image_url"] == "" and card["name"] == "Face-down card" and card["phased"] and card["rot"] and (card["dp"], card["dt"]) == (2, -1)
    assert private(client)["hidden"][a]["image_url"].startswith("https://cards.scryfall.io/")
    # counters of every kind, taken off again, or all at once
    client.patch(f"/api/play/cards/{b}", json={"counter": {"type": "Charge", "delta": 3}})
    client.patch(f"/api/play/cards/{b}", json={"counter": {"type": "Lifelink", "delta": 1}})
    client.patch(f"/api/play/cards/{b}", json={"counter": {"type": "Charge", "delta": -1}})
    client.post("/api/play/proliferate")
    counters = {k["t"]: k["n"] for k in next(c for c in state(client)["battlefield"] if c["uid"] == b)["counters"]}
    assert counters == {"Charge": 3, "Lifelink": 2}
    client.patch(f"/api/play/cards/{b}", json={"clear_counters": True})
    assert next(c for c in state(client)["battlefield"] if c["uid"] == b)["counters"] == []
    # tap all, token copies (they vanish when they leave), delete
    client.post("/api/play/tap-all")
    assert all(c["tapped"] for c in state(client)["battlefield"])
    client.post(f"/api/play/cards/{b}/copy", json={"count": 3})
    copies = [c for c in state(client)["battlefield"] if c["token"]]
    assert len(copies) == 3
    client.post("/api/play/move", json={"uid": copies[0]["uid"], "to": "graveyard"})
    assert not state(client)["graveyard"] and len([c for c in state(client)["battlefield"] if c["token"]]) == 2
    assert client.delete(f"/api/play/cards/{b}").status_code == 200
    assert client.delete(f"/api/play/cards/{b}").status_code == 404
    assert client.post("/api/play/cards/nope/copy").status_code == 404


def test_turns_counters_dice_and_the_log(client):
    client.post("/api/play/deck", json=deck())
    first = private(client)["hand"][0]["uid"]
    client.post("/api/play/move", json={"uid": first, "to": "battlefield"})
    client.post("/api/play/tap-all")
    hand = state(client)["hand"]
    client.patch("/api/play/life", json={"life_delta": -5, "poison_delta": 2, "energy_delta": 3, "experience_delta": 1, "storm_delta": 2, "tax_delta": 2})
    now = state(client)
    assert (now["life"], now["poison"], now["energy"], now["experience"], now["storm"], now["tax"]) == (35, 2, 3, 1, 2, 2)
    client.patch("/api/play/mana", json={"color": "G", "delta": 3})
    assert private(client)["mana"]["G"] == 3
    assert client.patch("/api/play/mana", json={"color": "X", "delta": 1}).status_code == 422
    client.post("/api/play/next-turn")
    now = state(client)
    assert now["turn"] == 2 and now["hand"] == hand + 1 and now["storm"] == 0 and not now["battlefield"][0]["tapped"]
    assert private(client)["mana"]["G"] == 0
    client.post("/api/play/dice", json={"kind": "d20"})
    assert 1 <= state(client)["roll"]["result"] <= 20
    client.post("/api/play/dice", json={"kind": "coin"})
    assert state(client)["roll"]["result"] in ("heads", "tails")
    assert client.post("/api/play/dice", json={"kind": "d7"}).status_code == 422
    assert "log" not in state(client) and any("Rolled" in line for line in private(client)["log"])


def test_the_hand_actions_and_undo(client):
    client.post("/api/play/deck", json=deck())
    state_of = lambda: state(client)  # noqa: E731
    client.post("/api/play/discard-random")
    assert len(state_of()["graveyard"]) == 1 and state_of()["hand"] == 6
    client.post("/api/play/order-hand", json={"by": "name"})
    names = [c["name"].lower() for c in private(client)["hand"]]
    assert names == sorted(names)
    client.post("/api/play/order-hand", json={"by": "type"})
    assert client.post("/api/play/order-hand", json={"by": "colour"}).status_code == 422
    client.post("/api/play/move-all", json={"zone": "hand", "to": "library-bottom"})
    assert state_of()["hand"] == 0
    client.post("/api/play/move-all", json={"zone": "graveyard", "to": "exile"})
    assert state_of()["exile"] and not state_of()["graveyard"]
    assert client.post("/api/play/move-all", json={"zone": "attic", "to": "exile"}).status_code == 422
    # undo and redo walk back and forth
    assert private(client)["can_undo"] is True
    client.post("/api/play/undo")
    assert state_of()["graveyard"] and not state_of()["exile"]
    client.post("/api/play/undo")
    assert state_of()["hand"] == 6
    assert private(client)["can_redo"] is True
    client.post("/api/play/redo")
    assert state_of()["hand"] == 0
    client.post("/api/play/undo")
    client.post("/api/play/undo")
    client.post("/api/play/undo")
    assert private(client)["can_undo"] in (True, False)


def test_library_draw_mulligan_and_a_new_game(client):
    client.post("/api/play/deck", json=deck())
    client.post("/api/play/draw", json={"count": 2})
    assert (state(client)["hand"], state(client)["library"]) == (9, 8)
    client.post("/api/play/mulligan")
    assert state(client)["hand"] == 7 and private(client)["mulligans"] == 1
    client.post("/api/play/shuffle")
    client.patch("/api/play/life", json={"life": 12})
    client.post("/api/play/new")
    now = state(client)
    assert (now["life"], now["hand"], now["turn"]) == (40, 7, 1) and private(client)["opening"] is True
    client.post("/api/play/clear")
    assert state(client)["loaded"] is False


def test_the_game_survives_a_restart_and_only_takes_scryfall_pictures(state_path):
    with TestClient(create_app(state_path)) as c:
        c.post("/api/play/deck", json=deck())
        first = private(c)["hand"][0]["uid"]
        c.post("/api/play/move", json={"uid": first, "to": "battlefield"})
        c.patch(f"/api/play/cards/{first}", json={"counter": {"type": "Time", "delta": 1}, "facedown": True})
        bad = {**deck(), "cards": [{"name": "X", "image_url": "https://evil.example/x.jpg", "count": 1}]}
        assert c.post("/api/play/deck", json=bad).status_code == 422
    with TestClient(create_app(state_path)) as c:
        play = state(c)
        assert play["loaded"] and play["hand"] == 6 and len(play["battlefield"]) == 1
        assert play["battlefield"][0]["counters"] == [{"t": "Time", "n": 1}] and play["battlefield"][0]["facedown"]
    state_path.with_name("state-play.json").write_text("{broken", encoding="utf-8")
    with TestClient(create_app(state_path)) as c:
        assert state(c)["loaded"] is False


def test_the_overlay_rows_by_type(client):
    client.post("/api/play/deck", json=deck())
    for card in private(client)["hand"]:
        client.post("/api/play/move", json={"uid": card["uid"], "to": "battlefield"})
    rows = {c["type"] for c in state(client)["battlefield"]}
    assert rows <= {"land", "creature", "other"}


def test_a_saved_deck_goes_to_the_playtest_table(monkeypatch):
    from fastapi.testclient import TestClient as AppClient

    from app.deps import get_scryfall
    from app.main import app as manager, stream_app
    from app.scryfall import ScryfallClient
    from app.stream import server as stream_server
    from tests.conftest import FakeScryfall

    fake = FakeScryfall()
    manager.dependency_overrides[get_scryfall] = lambda: ScryfallClient(transport=httpx.MockTransport(fake), min_interval=0)
    monkeypatch.setattr(stream_server, "ALLOWED_IMAGE_PREFIX", ("https://cards.scryfall.io/", "https://img.example/"))
    try:
        with AppClient(manager) as c:
            made = c.post("/api/decks", json={"name": "Virtual", "decklist": "1 Pitiless Plunderer\n3 Raise the Alarm"}).json()
            sent = c.post(f"/api/decks/{made['id']}/playtest")
            assert sent.status_code == 200 and sent.json()["deck"] == "Virtual" and sent.json()["cards"] == 4
            play = c.get("/stream/api/state").json()["play"]
            assert play["loaded"] and play["deck"] == "Virtual" and play["hand"] + play["library"] == 4
            r = c.post(f"/decks/{made['id']}/playtest", follow_redirects=False)
            assert r.status_code == 303 and r.headers["location"] == "/stream/playtest"
            assert c.post("/api/decks/99999/playtest").status_code == 404
            page = c.get(f"/decks/{made['id']}").text
            assert f'action="/decks/{made["id"]}/playtest"' in page and "♠ Playtest" in page
    finally:
        manager.dependency_overrides.clear()
        stream_app.state.play.clear()
