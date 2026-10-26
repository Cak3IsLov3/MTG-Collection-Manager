"""The token overlay for OBS (app/stream): REST API, limits, WebSocket and the files it keeps."""

import httpx
import pytest
from fastapi.testclient import TestClient

from app.stream.server import MAX_COUNT, MAX_TOKENS, create_app

IMG = "https://cards.scryfall.io/normal/front/a/b/{}.jpg"


def token(name="Treasure", n=0):
    return {"name": name, "image_url": IMG.format(n)}


@pytest.fixture
def state_path(tmp_path):
    return tmp_path / "state.json"


@pytest.fixture
def client(state_path):
    with TestClient(create_app(state_path)) as c:
        yield c


def tokens(client):
    r = client.get("/api/state")
    assert r.status_code == 200
    return r.json()["tokens"]


def test_add_token(client):
    r = client.post("/api/tokens", json=token())
    assert r.status_code == 201
    [t] = tokens(client)
    assert t["name"] == "Treasure"
    assert t["count"] == 1
    assert t["image_url"] == IMG.format(0)


def test_add_same_token_increments(client):
    client.post("/api/tokens", json=token())
    client.post("/api/tokens", json=token())
    [t] = tokens(client)
    assert t["count"] == 2


def test_patch_up_down_and_remove_at_zero(client):
    client.post("/api/tokens", json=token())
    tid = tokens(client)[0]["id"]

    assert client.patch(f"/api/tokens/{tid}", json={"delta": 1}).status_code == 200
    assert tokens(client)[0]["count"] == 2

    client.patch(f"/api/tokens/{tid}", json={"delta": -1})
    assert tokens(client)[0]["count"] == 1

    client.patch(f"/api/tokens/{tid}", json={"delta": -1})
    assert tokens(client) == []


def test_delete_and_clear(client):
    client.post("/api/tokens", json=token("A", 1))
    client.post("/api/tokens", json=token("B", 2))
    tid = tokens(client)[0]["id"]

    assert client.delete(f"/api/tokens/{tid}").status_code == 200
    assert len(tokens(client)) == 1

    assert client.post("/api/clear").status_code == 200
    assert tokens(client) == []


def test_unknown_id_gives_404(client):
    assert client.delete("/api/tokens/nope").status_code == 404
    assert client.patch("/api/tokens/nope", json={"delta": 1}).status_code == 404


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example.com/x.jpg",
        "http://cards.scryfall.io/normal/x.jpg",
        "https://cards.scryfall.io.evil.com/x.jpg",
        "javascript:alert(1)",
    ],
)
def test_foreign_image_url_rejected(client, url):
    r = client.post("/api/tokens", json={"name": "Bad", "image_url": url})
    assert r.status_code == 422
    assert tokens(client) == []


def test_max_distinct_tokens(client):
    for i in range(MAX_TOKENS):
        assert client.post("/api/tokens", json=token(f"T{i}", i)).status_code == 201
    r = client.post("/api/tokens", json=token("Extra", 999))
    assert r.status_code == 409
    assert len(tokens(client)) == MAX_TOKENS
    # Adding to an existing stack is still allowed.
    assert client.post("/api/tokens", json=token("T0", 0)).status_code == 201


def test_set_exact_count(client):
    tid = add_stack(client, 2)
    assert client.patch(f"/api/tokens/{tid}", json={"count": 7}).status_code == 200
    assert tokens(client)[0]["count"] == 7
    client.patch(f"/api/tokens/{tid}", json={"count": 999})
    assert tokens(client)[0]["count"] == 999
    assert client.patch(f"/api/tokens/{tid}", json={"count": 1000}).status_code == 422
    assert client.patch(f"/api/tokens/{tid}", json={"count": -1}).status_code == 422
    client.patch(f"/api/tokens/{tid}", json={"count": 0})
    assert tokens(client) == []


def test_count_capped_at_max(client):
    client.post("/api/tokens", json=token())
    tid = tokens(client)[0]["id"]
    client.patch(f"/api/tokens/{tid}", json={"delta": MAX_COUNT})
    assert tokens(client)[0]["count"] == MAX_COUNT
    client.post("/api/tokens", json=token())
    client.patch(f"/api/tokens/{tid}", json={"delta": 5})
    assert tokens(client)[0]["count"] == MAX_COUNT


def add_stack(client, n, name="Goblin", img=0):
    for _ in range(n):
        client.post("/api/tokens", json=token(name, img))
    return tokens(client)[-1]["id"]


def test_split_creates_new_stack_next_to_original(client):
    tid = add_stack(client, 5)
    client.post("/api/tokens", json=token("Clue", 9))
    r = client.post(f"/api/tokens/{tid}/split", json={"count": 2})
    assert r.status_code == 200
    a, b, c = tokens(client)
    assert (a["id"], a["count"]) == (tid, 3)
    assert b["id"] != tid and b["count"] == 2 and b["image_url"] == a["image_url"]
    assert c["name"] == "Clue"


def test_split_keeps_tapped_and_counters(client):
    tid = add_stack(client, 3)
    client.patch(f"/api/tokens/{tid}", json={"tapped": True, "counter_delta": 2})
    client.post(f"/api/tokens/{tid}/split", json={"count": 1})
    _, new = tokens(client)
    assert new["tapped"] is True and new["counters"] == 2


@pytest.mark.parametrize("count", [3, 4, 0, -1])
def test_split_invalid_count(client, count):
    tid = add_stack(client, 3)
    r = client.post(f"/api/tokens/{tid}/split", json={"count": count})
    assert r.status_code == 422
    assert len(tokens(client)) == 1


def test_split_unknown_and_limit(client):
    assert client.post("/api/tokens/nope/split", json={"count": 1}).status_code == 404
    for i in range(MAX_TOKENS - 1):
        client.post("/api/tokens", json=token(f"T{i}", i))
    tid = add_stack(client, 2, "Big", 500)
    assert client.post(f"/api/tokens/{tid}/split", json={"count": 1}).status_code == 409


def test_tap_and_untap(client):
    tid = add_stack(client, 1)
    assert tokens(client)[0]["tapped"] is False
    client.patch(f"/api/tokens/{tid}", json={"tapped": True})
    assert tokens(client)[0]["tapped"] is True
    client.patch(f"/api/tokens/{tid}", json={"tapped": False})
    assert tokens(client)[0]["tapped"] is False


def test_untap_all(client):
    a = add_stack(client, 1, "A", 1)
    b = add_stack(client, 1, "B", 2)
    client.patch(f"/api/tokens/{a}", json={"tapped": True})
    client.patch(f"/api/tokens/{b}", json={"tapped": True})
    client.post("/api/untap")
    assert [t["tapped"] for t in tokens(client)] == [False, False]


def test_counters_cancel_out_and_are_capped(client):
    tid = add_stack(client, 1)
    client.patch(f"/api/tokens/{tid}", json={"counter_delta": 1})
    client.patch(f"/api/tokens/{tid}", json={"counter_delta": 1})
    assert tokens(client)[0]["counters"] == 2
    client.patch(f"/api/tokens/{tid}", json={"counter_delta": -3})
    assert tokens(client)[0]["counters"] == -1
    client.patch(f"/api/tokens/{tid}", json={"counter_delta": 99})
    client.patch(f"/api/tokens/{tid}", json={"counter_delta": 99})
    assert tokens(client)[0]["counters"] == 99
    assert client.patch(f"/api/tokens/{tid}", json={"counter_delta": 500}).status_code == 422


def test_add_does_not_join_tapped_or_countered_stack(client):
    tid = add_stack(client, 2)
    client.patch(f"/api/tokens/{tid}", json={"tapped": True})
    client.post("/api/tokens", json=token("Goblin", 0))
    stacks = tokens(client)
    assert [(t["count"], t["tapped"]) for t in stacks] == [(2, True), (1, False)]
    # Adding again goes to the untapped stack.
    client.post("/api/tokens", json=token("Goblin", 0))
    assert tokens(client)[1]["count"] == 2


def test_merge_joins_identical_stacks_only(client):
    tid = add_stack(client, 6)
    client.post(f"/api/tokens/{tid}/split", json={"count": 2})
    client.post(f"/api/tokens/{tid}/split", json={"count": 1})
    # Order is now [tid: 3, new: 1, new: 2]; tap the stack of 2.
    tapped_id = tokens(client)[2]["id"]
    client.patch(f"/api/tokens/{tapped_id}", json={"tapped": True})

    assert client.post(f"/api/tokens/{tid}/merge").status_code == 200
    stacks = tokens(client)
    assert [(t["id"], t["count"]) for t in stacks] == [(tid, 4), (tapped_id, 2)]
    assert stacks[1]["tapped"] is True
    assert client.post("/api/tokens/nope/merge").status_code == 404


def test_new_stack_has_no_position(client):
    add_stack(client, 1)
    assert tokens(client)[0]["pos"] is None


def test_set_and_reset_position(client):
    tid = add_stack(client, 1)
    r = client.patch(f"/api/tokens/{tid}", json={"pos": {"x": 0.25, "y": 0.5}})
    assert r.status_code == 200
    assert tokens(client)[0]["pos"] == {"x": 0.25, "y": 0.5}

    # Other changes leave the position alone.
    client.patch(f"/api/tokens/{tid}", json={"tapped": True})
    assert tokens(client)[0]["pos"] == {"x": 0.25, "y": 0.5}

    client.patch(f"/api/tokens/{tid}", json={"pos": None})
    assert tokens(client)[0]["pos"] is None


@pytest.mark.parametrize("pos", [{"x": -0.1, "y": 0.5}, {"x": 0.5, "y": 1.5}, {"x": 0.5}])
def test_invalid_position_rejected(client, pos):
    tid = add_stack(client, 1)
    assert client.patch(f"/api/tokens/{tid}", json={"pos": pos}).status_code == 422
    assert tokens(client)[0]["pos"] is None


def test_split_fans_out_placed_stack(client):
    tid = add_stack(client, 3)
    client.patch(f"/api/tokens/{tid}", json={"pos": {"x": 0.5, "y": 0.2}})
    client.post(f"/api/tokens/{tid}/split", json={"count": 1})
    orig, new = tokens(client)
    assert orig["pos"] == {"x": 0.5, "y": 0.2}
    assert new["pos"]["y"] == 0.2 and new["pos"]["x"] > 0.5


def test_split_of_unplaced_stack_stays_in_row(client):
    tid = add_stack(client, 3)
    client.post(f"/api/tokens/{tid}/split", json={"count": 1})
    assert [t["pos"] for t in tokens(client)] == [None, None]


def test_reset_all_positions(client):
    a = add_stack(client, 1, "A", 1)
    b = add_stack(client, 1, "B", 2)
    client.patch(f"/api/tokens/{a}", json={"pos": {"x": 0.1, "y": 0.1}})
    client.patch(f"/api/tokens/{b}", json={"pos": {"x": 0.9, "y": 0.9}})
    assert client.post("/api/positions/reset").status_code == 200
    assert [t["pos"] for t in tokens(client)] == [None, None]


def test_keywords_stored_and_cleaned(client):
    body = {**token("Angel", 3), "keywords": [" Flying ", "Vigilance", "Flying", ""]}
    client.post("/api/tokens", json=body)
    assert tokens(client)[0]["keywords"] == ["Flying", "Vigilance"]


def test_too_many_keywords_rejected(client):
    body = {**token(), "keywords": [f"K{i}" for i in range(13)]}
    assert client.post("/api/tokens", json=body).status_code == 422


def test_manual_power_toughness(client):
    client.post("/api/tokens", json={**token("Ooze", 4), "power": "*", "toughness": "*"})
    tid = tokens(client)[0]["id"]
    assert (tokens(client)[0]["set_power"], tokens(client)[0]["set_toughness"]) == (None, None)

    client.patch(f"/api/tokens/{tid}", json={"set_power": 3, "set_toughness": 4})
    t = tokens(client)[0]
    assert (t["set_power"], t["set_toughness"]) == (3, 4)
    assert (t["power"], t["toughness"]) == ("*", "*")  # printed value is kept

    # Other changes leave it alone; null clears it.
    client.patch(f"/api/tokens/{tid}", json={"counter_delta": 1})
    assert tokens(client)[0]["set_power"] == 3
    client.patch(f"/api/tokens/{tid}", json={"set_power": None, "set_toughness": None})
    t = tokens(client)[0]
    assert (t["set_power"], t["set_toughness"]) == (None, None)
    assert client.patch(f"/api/tokens/{tid}", json={"set_power": 5000}).status_code == 422


def test_split_keeps_manual_pt_and_merge_respects_it(client):
    client.post("/api/tokens", json={**token("Ooze", 4), "power": "*", "toughness": "*"})
    client.post("/api/tokens", json={**token("Ooze", 4), "power": "*", "toughness": "*"})
    tid = tokens(client)[0]["id"]
    client.patch(f"/api/tokens/{tid}", json={"set_power": 2, "set_toughness": 2})
    client.post(f"/api/tokens/{tid}/split", json={"count": 1})
    a, b = tokens(client)
    assert b["set_power"] == 2
    # Different manual P/T: not the same kind, so merge leaves them apart.
    client.patch(f"/api/tokens/{b['id']}", json={"set_power": 5})
    client.post(f"/api/tokens/{tid}/merge")
    assert len(tokens(client)) == 2


def test_add_loose_counters(client):
    assert client.post("/api/counters", json={"type": "+1/+1"}).status_code == 201
    client.post("/api/counters", json={"type": "+1/+1"})
    client.post("/api/counters", json={"type": "-1/-1"})
    stacks = tokens(client)
    # A +1/+1 or -1/-1 counter is a counter of its own every time; other counters pile up.
    assert [(t["kind"], t["name"], t["count"], t["image_url"]) for t in stacks] == [
        ("counter", "+1/+1", 1, ""),
        ("counter", "+1/+1", 1, ""),
        ("counter", "-1/-1", 1, ""),
    ]
    client.post("/api/counters", json={"type": "Flying"})
    client.post("/api/counters", json={"type": "Flying"})
    client.post("/api/counters", json={"type": "R"})
    client.post("/api/counters", json={"type": "R"})
    assert [(t["name"], t["count"]) for t in tokens(client)][3:] == [("Flying", 2), ("R", 2)]


@pytest.mark.parametrize("body", [{"type": "poison"}, {"type": ""}, {}])
def test_invalid_counter_type(client, body):
    assert client.post("/api/counters", json=body).status_code == 422


def test_loose_counters_count_move_split_delete(client):
    client.post("/api/counters", json={"type": "+1/+1"})
    cid = tokens(client)[0]["id"]
    client.patch(f"/api/tokens/{cid}", json={"delta": 3, "pos": {"x": 0.5, "y": 0.5}})
    assert tokens(client)[0]["count"] == 4
    client.post(f"/api/tokens/{cid}/split", json={"count": 2})
    assert [t["count"] for t in tokens(client)] == [2, 2]
    client.post(f"/api/tokens/{cid}/merge")
    assert [t["count"] for t in tokens(client)] == [4]
    client.delete(f"/api/tokens/{cid}")
    assert tokens(client) == []


def test_keyword_counters(client):
    client.post("/api/counters", json={"type": "Flying"})
    client.post("/api/counters", json={"type": "Flying"})
    client.post("/api/counters", json={"type": "Double strike"})
    assert [(t["name"], t["count"]) for t in tokens(client)] == [("Flying", 2), ("Double strike", 1)]
    assert client.post("/api/counters", json={"type": "flying"}).status_code == 422


def test_ward_value(client):
    client.post("/api/counters", json={"type": "Ward"})
    wid = tokens(client)[0]["id"]
    assert tokens(client)[0]["value"] is None
    client.patch(f"/api/tokens/{wid}", json={"value": " 2 "})
    assert tokens(client)[0]["value"] == "2"
    client.patch(f"/api/tokens/{wid}", json={"value": "Pay 3 life"})
    assert tokens(client)[0]["value"] == "Pay 3 life"
    client.patch(f"/api/tokens/{wid}", json={"value": ""})
    assert tokens(client)[0]["value"] is None
    assert client.patch(f"/api/tokens/{wid}", json={"value": "x" * 25}).status_code == 422


def test_ward_piles_with_different_values_stay_apart(client):
    client.post("/api/counters", json={"type": "Ward", "value": "2"})
    client.post("/api/counters", json={"type": "Ward", "value": "2"})
    client.post("/api/counters", json={"type": "Ward", "value": "4"})
    client.post("/api/counters", json={"type": "Ward"})
    assert [(t["value"], t["count"]) for t in tokens(client)] == [("2", 2), ("4", 1), (None, 1)]
    # Same type but a different value: merge leaves them apart.
    client.post(f"/api/tokens/{tokens(client)[0]['id']}/merge")
    assert len(tokens(client)) == 3


def test_mana_pool(client):
    for m in ["R", "R", "R", "G", "C"]:
        assert client.post("/api/counters", json={"type": m}).status_code == 201
    client.post("/api/tokens", json=token())
    client.post("/api/counters", json={"type": "+1/+1"})
    assert [(t["name"], t["count"]) for t in tokens(client) if t["kind"] == "counter"] == [
        ("R", 3), ("G", 1), ("C", 1), ("+1/+1", 1),
    ]
    # Emptying the pool only removes mana, not cards or other counters.
    assert client.post("/api/mana/empty").status_code == 200
    assert [t["name"] for t in tokens(client)] == ["Treasure", "+1/+1"]
    assert client.post("/api/counters", json={"type": "X"}).status_code == 422


def test_counter_types_endpoint(client):
    types = client.get("/api/counter-types").json()
    assert types["pt"] == ["+1/+1", "-1/-1"]
    assert "Trample" in types["keywords"]
    assert types["mana"] == ["W", "U", "B", "R", "G", "C"]


def test_cards_and_counters_stay_apart(client):
    client.post("/api/tokens", json=token())
    client.post("/api/counters", json={"type": "+1/+1"})
    client.post("/api/tokens", json=token())
    assert [(t["kind"], t["count"]) for t in tokens(client)] == [("card", 2), ("counter", 1)]


def test_power_toughness_stored(client):
    client.post("/api/tokens", json={**token(), "power": "1", "toughness": "1"})
    t = tokens(client)[0]
    assert (t["power"], t["toughness"]) == ("1", "1")


def test_old_state_file_without_new_fields(state_path):
    state_path.write_text(
        '{"tokens": [{"id": "x", "name": "Old", "image_url": "%s", "count": 2}]}' % IMG.format(1),
        encoding="utf-8",
    )
    with TestClient(create_app(state_path)) as c:
        [t] = tokens(c)
        assert t["count"] == 2 and t["tapped"] is False and t["counters"] == 0


def test_websocket_receives_state_and_updates(client):
    with client.websocket_connect("/ws") as ws:
        first = ws.receive_json()
        assert first["tokens"] == []
        assert first["server"]
        client.post("/api/tokens", json=token("Goblin", 7))
        msg = ws.receive_json()
        assert [t["name"] for t in msg["tokens"]] == ["Goblin"]
        assert msg["tokens"][0]["count"] == 1


@pytest.mark.parametrize("kind,sides", [("d4", 4), ("d6", 6), ("d20", 20)])
def test_roll_dice(client, kind, sides):
    for _ in range(30):
        ev = client.post("/api/roll", json={"kind": kind, "count": 3}).json()
        assert ev["type"] == "roll" and ev["kind"] == kind
        assert len(ev["results"]) == 3
        assert all(1 <= r <= sides for r in ev["results"])
        assert ev["total"] == sum(ev["results"])


def test_roll_coin(client):
    seen = set()
    for _ in range(40):
        ev = client.post("/api/roll", json={"kind": "coin"}).json()
        assert len(ev["results"]) == 1 and "total" not in ev
        seen.update(ev["results"])
    assert seen == {"heads", "tails"}


@pytest.mark.parametrize("body", [{"kind": "d100"}, {"kind": "d6", "count": 0}, {"kind": "d6", "count": 7}, {}])
def test_roll_invalid(client, body):
    assert client.post("/api/roll", json=body).status_code == 422


def test_roll_is_broadcast_and_not_stored(client):
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ev = client.post("/api/roll", json={"kind": "d20"}).json()
        msg = ws.receive_json()
        assert msg["event"] == ev
        assert "tokens" not in msg
    assert tokens(client) == []


def test_use_treasure(client):
    tid = add_stack(client, 2, "Treasure")
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        assert client.post(f"/api/tokens/{tid}/use").status_code == 200
        first, second = ws.receive_json(), ws.receive_json()
    assert first["event"] == {"type": "use", "id": tid, "name": "Treasure",
                              "tap": True, "sac": True, "text": "+1 mana"}
    assert second["tokens"][0]["count"] == 1
    client.post(f"/api/tokens/{tid}/use")
    assert tokens(client) == []


def test_use_needs_untapped_when_it_costs_tap(client):
    tid = add_stack(client, 1, "Food")
    client.patch(f"/api/tokens/{tid}", json={"tapped": True})
    assert client.post(f"/api/tokens/{tid}/use").status_code == 409
    assert tokens(client)[0]["count"] == 1
    # Clue doesn't cost {T}: fine while tapped.
    cid = add_stack(client, 1, "Clue", 5)
    client.patch(f"/api/tokens/{cid}", json={"tapped": True})
    assert client.post(f"/api/tokens/{cid}/use").status_code == 200


def test_use_powerstone_taps_instead_of_sacrificing(client):
    pid = add_stack(client, 3, "Powerstone")
    client.post(f"/api/tokens/{pid}/use")
    cards = [t for t in tokens(client) if t["kind"] == "card"]
    assert [(t["count"], t["tapped"]) for t in cards] == [(2, False), (1, True)]
    # ...and {C} goes into the mana pool.
    assert [(t["name"], t["count"]) for t in tokens(client) if t["kind"] == "counter"] == [("C", 1)]
    one = add_stack(client, 1, "Powerstone", 9)
    client.post(f"/api/tokens/{one}/use")
    last_card = [t for t in tokens(client) if t["kind"] == "card"][-1]
    assert last_card["tapped"] is True and last_card["count"] == 1


def test_use_without_ability(client):
    tid = add_stack(client, 1, "Goblin")
    assert client.post(f"/api/tokens/{tid}/use").status_code == 422
    client.post("/api/counters", json={"type": "+1/+1"})
    cid = tokens(client)[-1]["id"]
    assert client.post(f"/api/tokens/{cid}/use").status_code == 422
    assert client.post("/api/tokens/nope/use").status_code == 404
    assert "treasure" in client.get("/api/abilities").json()


def test_sacrifice_one_and_all(client):
    tid = add_stack(client, 3, "Treasure")
    assert client.post(f"/api/tokens/{tid}/sacrifice", json={"count": 1}).status_code == 200
    assert tokens(client)[0]["count"] == 2
    # Asking for more than the stack holds sacrifices the whole stack.
    client.post(f"/api/tokens/{tid}/sacrifice", json={"count": 10})
    assert tokens(client) == []


def test_sacrifice_event_comes_before_state(client):
    tid = add_stack(client, 2, "Food")
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        client.post(f"/api/tokens/{tid}/sacrifice", json={"count": 1})
        first, second = ws.receive_json(), ws.receive_json()
    assert first["event"] == {"type": "sacrifice", "id": tid, "name": "Food", "count": 1}
    assert second["tokens"][0]["count"] == 1


def test_sacrifice_unknown_or_counter(client):
    assert client.post("/api/tokens/nope/sacrifice", json={"count": 1}).status_code == 404
    client.post("/api/counters", json={"type": "+1/+1"})
    cid = tokens(client)[0]["id"]
    assert client.post(f"/api/tokens/{cid}/sacrifice", json={"count": 1}).status_code == 422
    assert tokens(client)[0]["count"] == 1


def test_version_up_to_date(client):
    v = client.get("/api/version").json()
    assert v["outdated"] is False
    assert v["running"] == v["on_disk"]


def test_version_outdated_when_code_changes(client, monkeypatch):
    from app.stream import server as main
    monkeypatch.setattr(main, "code_fingerprint", lambda: "changed123")
    v = client.get("/api/version").json()
    assert v["outdated"] is True and v["on_disk"] == "changed123"


def creature(client, n=1, name="Goblin", img=0, **extra):
    for _ in range(n):
        client.post("/api/tokens", json={**token(name, img), "power": "1", "toughness": "1", **extra})
    return tokens(client)[-1]["id"]


def test_attack_taps_creature(client):
    gid = creature(client, 2)
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        assert client.post(f"/api/tokens/{gid}/attack").status_code == 200
        event, state = ws.receive_json(), ws.receive_json()
    assert event["event"]["type"] == "attack" and event["event"]["ids"] == [gid]
    assert state["tokens"][0]["tapped"] is True
    assert client.post(f"/api/tokens/{gid}/attack").status_code == 409


def test_attack_vigilance_and_non_creature(client):
    aid = creature(client, 1, "Angel", 3, keywords=["Flying", "Vigilance"])
    client.post(f"/api/tokens/{aid}/attack")
    assert tokens(client)[0]["tapped"] is False      # vigilance: attacks without tapping
    tid = add_stack(client, 1, "Treasure", 4)
    assert client.post(f"/api/tokens/{tid}/attack").status_code == 422


def test_attack_all(client):
    creature(client, 2, "Goblin", 1)
    tapped = creature(client, 1, "Zombie", 2)
    client.patch(f"/api/tokens/{tapped}", json={"tapped": True})
    add_stack(client, 1, "Treasure", 3)
    creature(client, 1, "Angel", 4, keywords=["Vigilance"])
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        client.post("/api/attack-all")
        event = ws.receive_json()["event"]
    by_name = {t["name"]: t for t in tokens(client)}
    assert len(event["ids"]) == 2                     # Goblins and the Angel; not the tapped Zombie
    assert by_name["Goblin"]["tapped"] and not by_name["Angel"]["tapped"]
    assert not by_name["Treasure"]["tapped"]


def test_copy_and_double(client):
    gid = creature(client, 3)
    client.patch(f"/api/tokens/{gid}", json={"tapped": True, "counter_delta": 1})
    client.post(f"/api/tokens/{gid}/copy", json={"count": 1})
    stacks = tokens(client)
    assert [(t["count"], t["tapped"], t["counters"]) for t in stacks] == [(3, True, 1), (1, False, 0)]
    # Double the fresh stack: copies join it.
    client.post(f"/api/tokens/{stacks[1]['id']}/copy", json={"count": 1})
    assert tokens(client)[1]["count"] == 2
    client.post("/api/counters", json={"type": "R"})
    cid = tokens(client)[-1]["id"]
    assert client.post(f"/api/tokens/{cid}/copy", json={"count": 1}).status_code == 422


def test_untap_event(client):
    gid = creature(client, 1)
    client.patch(f"/api/tokens/{gid}", json={"tapped": True})
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        client.post("/api/untap")
        assert ws.receive_json()["event"] == {"type": "untap", "count": 1}
    assert tokens(client)[0]["tapped"] is False


def test_treasure_color_goes_to_mana_pool(client):
    tid = add_stack(client, 2, "Treasure")
    client.post(f"/api/tokens/{tid}/use", json={"color": "R"})
    client.post(f"/api/tokens/{tid}/use", json={"color": "R"})
    assert [(t["name"], t["count"]) for t in tokens(client)] == [("R", 2)]
    gid = add_stack(client, 1, "Gold", 5)
    client.post(f"/api/tokens/{gid}/use")            # no colour: nothing in the pool
    assert [t["name"] for t in tokens(client)] == ["R"]
    fid = add_stack(client, 1, "Food", 6)
    assert client.post(f"/api/tokens/{fid}/use", json={"color": "X"}).status_code == 422


def test_undo(client):
    assert client.post("/api/undo").status_code == 409
    tid = add_stack(client, 2, "Treasure")
    client.patch(f"/api/tokens/{tid}", json={"tapped": True})
    client.post(f"/api/tokens/{tid}/sacrifice", json={"count": 2})
    assert tokens(client) == []
    client.post("/api/undo")                         # sacrifice undone
    assert [(t["count"], t["tapped"]) for t in tokens(client)] == [(2, True)]
    client.post("/api/undo")                         # tap undone
    assert tokens(client)[0]["tapped"] is False


def test_undo_skips_moves_and_keeps_positions(client):
    tid = add_stack(client, 1)
    client.patch(f"/api/tokens/{tid}", json={"tapped": True})
    for x in (0.1, 0.2, 0.3):                        # dragging: not undo steps
        client.patch(f"/api/tokens/{tid}", json={"pos": {"x": x, "y": 0.5}})
    client.post("/api/undo")                         # undoes the tap, keeps the position
    t = tokens(client)[0]
    assert t["tapped"] is False and t["pos"] == {"x": 0.3, "y": 0.5}


def test_failed_change_is_not_an_undo_step(client):
    tid = add_stack(client, 1, "Food")
    client.patch(f"/api/tokens/{tid}", json={"tapped": True})
    assert client.post(f"/api/tokens/{tid}/use").status_code == 409   # already tapped
    client.post("/api/undo")
    assert tokens(client)[0]["tapped"] is False      # the undo went to the tap


def test_presets(client, state_path):
    creature(client, 3, "Goblin", 1)
    tid = add_stack(client, 2, "Treasure", 2)
    client.patch(f"/api/tokens/{tid}", json={"tapped": True})
    r = client.post("/api/presets", json={"name": " Krenko "})
    assert r.status_code == 201 and r.json() == [{"name": "Krenko", "stacks": 2, "tokens": 5}]
    client.post("/api/clear")
    client.post("/api/presets/Krenko/load")
    loaded = tokens(client)
    assert [(t["name"], t["count"], t["tapped"]) for t in loaded] == [("Goblin", 3, False), ("Treasure", 2, False)]
    # Loading is an undo step too.
    client.post("/api/undo")
    assert tokens(client) == []
    # Saved in their own file, next to the state file.
    assert (state_path.parent / "state-presets.json").exists()
    assert client.post("/api/presets/Nope/load").status_code == 404
    assert client.delete("/api/presets/Krenko").json() == []
    assert client.post("/api/presets", json={"name": "  "}).status_code == 422


def test_deck_and_preset_changes_are_announced(client, fake_net):
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        client.post("/api/deck", json={"url": "https://archidekt.com/decks/1234"})
        assert ws.receive_json()["event"] == {"type": "deck"}
        add_stack(client, 1)
        ws.receive_json()                                  # the board update
        client.post("/api/presets", json={"name": "P"})
        assert ws.receive_json()["event"] == {"type": "presets"}
        client.delete("/api/presets/P")
        assert ws.receive_json()["event"] == {"type": "presets"}


def test_presets_survive_restart(state_path):
    with TestClient(create_app(state_path)) as c:
        add_stack(c, 1, "Clue", 1)
        c.post("/api/presets", json={"name": "Clues"})
    with TestClient(create_app(state_path)) as c:
        assert [p["name"] for p in c.get("/api/presets").json()] == ["Clues"]


# ---- deck import (network faked)

IMG_A = "https://cards.scryfall.io/normal/front/1/1/aaa.jpg"
IMG_B = "https://cards.scryfall.io/normal/front/2/2/bbb.jpg"

ARCHIDEKT_DECK = {
    "name": "Krenko",
    "categories": [{"name": "Commander", "includedInDeck": True},
                   {"name": "Maybeboard", "includedInDeck": False}],
    "cards": [
        {"categories": ["Commander"], "card": {"uid": "krenko"}},
        {"categories": [], "card": {"uid": "pitiless"}},
        {"categories": ["Maybeboard"], "card": {"uid": "maybe"}},
    ],
}
SCRYFALL = {
    "krenko": {"id": "krenko", "name": "Krenko, Mob Boss",
               "all_parts": [{"component": "token", "id": "goblin"},
                             {"component": "combo_piece", "id": "krenko"}]},
    "pitiless": {"id": "pitiless", "name": "Pitiless Plunderer",
                 "all_parts": [{"component": "token", "id": "treasure"},
                               {"component": "token", "id": "goblin2"}]},
    "maybe": {"id": "maybe", "name": "Should Not Count",
              "all_parts": [{"component": "token", "id": "zombie"}]},
    "goblin": {"id": "goblin", "name": "Goblin", "power": "1", "toughness": "1", "keywords": [],
               "oracle_text": "", "type_line": "Token Creature — Goblin", "image_uris": {"normal": IMG_A}},
    # Another print of the same Goblin token: merged into one entry.
    "goblin2": {"id": "goblin2", "name": "Goblin", "power": "1", "toughness": "1", "keywords": [],
                "oracle_text": "", "type_line": "Token Creature — Goblin", "image_uris": {"normal": IMG_A}},
    "treasure": {"id": "treasure", "name": "Treasure", "keywords": [],
                 "oracle_text": "{T}, Sacrifice this artifact: Add one mana of any color.",
                 "type_line": "Token Artifact — Treasure", "image_uris": {"normal": IMG_B}},
}


@pytest.fixture
def fake_net(monkeypatch):
    from app.stream import server as main
    calls = []

    def http_json(url, body=None):
        calls.append(url)
        if url.startswith("https://archidekt.com/api/decks/1234/"):
            return ARCHIDEKT_DECK
        if url.startswith("https://api2.moxfield.com/v3/decks/all/AbC"):
            return {"name": "Mox", "boards": {
                "commanders": {"cards": {"x": {"card": {"scryfall_id": "krenko"}}}},
                "mainboard": {"cards": {}},
                "maybeboard": {"cards": {"y": {"card": {"scryfall_id": "maybe"}}}},
            }}
        if url == "https://api.scryfall.com/cards/collection":
            return {"data": [SCRYFALL[i["id"]] for i in body["identifiers"] if i["id"] in SCRYFALL]}
        raise main.DeckError(404, "deck not found (is it public?)")

    monkeypatch.setattr(main, "http_json", http_json)
    monkeypatch.setattr(main.time, "sleep", lambda s: None)
    return calls


def test_import_archidekt_deck(client, fake_net):
    r = client.post("/api/deck", json={"url": "https://archidekt.com/decks/1234/krenko_goblins"})
    assert r.status_code == 200
    deck = r.json()
    assert deck["name"] == "Krenko" and deck["source"] == "archidekt" and deck["cards"] == 2
    assert [(t["name"], t["made_by"]) for t in deck["tokens"]] == [
        ("Goblin", ["Krenko, Mob Boss", "Pitiless Plunderer"]),
        ("Treasure", ["Pitiless Plunderer"]),
    ]
    # Ready to add: same fields the board takes.
    goblin = deck["tokens"][0]
    assert client.post("/api/tokens", json={k: goblin[k] for k in ("name", "image_url", "power", "toughness", "keywords")}).status_code == 201
    # Remembered.
    assert client.get("/api/deck").json()["name"] == "Krenko"


def test_import_moxfield_deck(client, fake_net):
    deck = client.post("/api/deck", json={"url": "https://www.moxfield.com/decks/AbC"}).json()
    assert deck["source"] == "moxfield"
    assert [t["name"] for t in deck["tokens"]] == ["Goblin"]       # maybeboard ignored


@pytest.mark.parametrize("url", ["https://evil.example.com/decks/1", "http://localhost:8000/api/state",
                                 "archidekt.com/decks/abc", "https://moxfield.com/users/x"])
def test_import_rejects_other_links(client, fake_net, url):
    assert client.post("/api/deck", json={"url": url}).status_code == 400
    assert fake_net == []                                           # nothing was fetched


def test_import_unknown_deck(client, fake_net):
    r = client.post("/api/deck", json={"url": "https://archidekt.com/decks/9999"})
    assert r.status_code == 404 and "public" in r.json()["detail"]


def test_deck_survives_restart_and_can_be_forgotten(state_path, fake_net):
    with TestClient(create_app(state_path)) as c:
        c.post("/api/deck", json={"url": "https://archidekt.com/decks/1234"})
    with TestClient(create_app(state_path)) as c:
        assert c.get("/api/deck").json()["name"] == "Krenko"
        c.delete("/api/deck")
        assert c.get("/api/deck").json() is None


def test_token_info_ward_and_faces():
    from app.stream import server as main
    card = {"name": "A // B", "keywords": ["Flying", "Ward"], "card_faces": [
        {"name": "A", "power": "2", "toughness": "2", "oracle_text": "Flying\nWard {2}",
         "image_uris": {"normal": IMG_A}, "type_line": "Token Creature"},
        {"name": "B", "oracle_text": "Trample", "image_uris": {"normal": IMG_B}}]}
    info = main.token_info(card)
    assert info["name"] == "A" and info["power"] == "2"
    assert info["keywords"] == ["Flying", "Ward {2}"]


# ---- turn tracker

def turn(client):
    return client.get("/api/state").json()["turn"]


def test_turn_starts_off(client):
    assert turn(client) == {"on": False, "number": 1, "phase": "untap", "casts": 0, "storm": 0}


def test_next_phase_walks_through_the_turn(client):
    phases = []
    for _ in range(7):
        client.post("/api/turn/next")
        phases.append(turn(client)["phase"])
    assert phases == ["upkeep", "draw", "main1", "combat", "main2", "end", "untap"]
    assert turn(client)["number"] == 2 and turn(client)["on"] is True


def test_mana_empties_between_phases(client):
    client.post("/api/counters", json={"type": "R"})
    client.post("/api/counters", json={"type": "+1/+1"})
    client.post("/api/turn/next")
    assert [t["name"] for t in tokens(client)] == ["+1/+1"]      # only the mana went


def test_new_turn_untaps_with_events(client):
    tid = add_stack(client, 2)
    client.patch(f"/api/tokens/{tid}", json={"tapped": True})
    client.patch("/api/turn", json={"phase": "end", "on": True})
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        client.post("/api/turn/next")
        events = [ws.receive_json()["event"] for _ in range(3)]
    assert events[0] == {"type": "turn", "number": 2}
    assert events[1] == {"type": "untap", "count": 1}
    assert events[2]["type"] == "phase" and events[2]["phase"] == "untap"
    assert tokens(client)[0]["tapped"] is False


def test_prev_phase(client):
    client.patch("/api/turn", json={"phase": "draw", "number": 3})
    client.post("/api/turn/prev")
    assert turn(client)["phase"] == "upkeep"
    client.patch("/api/turn", json={"phase": "untap"})
    client.post("/api/turn/prev")                        # back into the previous turn
    assert (turn(client)["number"], turn(client)["phase"]) == (2, "end")
    client.patch("/api/turn", json={"phase": "untap", "number": 1})
    client.post("/api/turn/prev")                        # can't go before turn 1
    assert (turn(client)["number"], turn(client)["phase"]) == (1, "untap")


def test_patch_turn(client):
    client.patch("/api/turn", json={"on": True, "phase": "combat", "number": 5})
    assert turn(client) == {"on": True, "number": 5, "phase": "combat", "casts": 0, "storm": 0}
    client.patch("/api/turn", json={"on": False})
    assert turn(client)["on"] is False
    assert client.patch("/api/turn", json={"phase": "lunch"}).status_code == 422
    assert client.patch("/api/turn", json={"number": 0}).status_code == 422


def test_undo_next_phase_brings_mana_and_phase_back(client):
    client.post("/api/counters", json={"type": "G"})
    client.post("/api/turn/next")
    assert tokens(client) == []
    client.post("/api/undo")
    assert turn(client)["phase"] == "untap"
    assert [t["name"] for t in tokens(client)] == ["G"]


def test_turn_survives_restart(state_path):
    with TestClient(create_app(state_path)) as c:
        c.patch("/api/turn", json={"on": True, "phase": "main2", "number": 4})
    with TestClient(create_app(state_path)) as c:
        assert turn(c) == {"on": True, "number": 4, "phase": "main2", "casts": 0, "storm": 0}


def test_broken_turn_in_state_file(state_path):
    state_path.write_text('{"tokens": [], "turn": {"on": "yes", "number": -3, "phase": "nap"}}', encoding="utf-8")
    with TestClient(create_app(state_path)) as c:
        assert turn(c) == {"on": False, "number": 1, "phase": "untap", "casts": 0, "storm": 0}


def test_pages_not_cached(client):
    for path in ("/control", "/overlay"):
        r = client.get(path)
        assert r.status_code == 200
        assert r.headers["cache-control"] == "no-store"


def test_server_id_changes_on_restart(state_path):
    ids = []
    for _ in range(2):
        with TestClient(create_app(state_path)) as c, c.websocket_connect("/ws") as ws:
            ids.append(ws.receive_json()["server"])
    assert ids[0] != ids[1]


def test_persistence(state_path):
    with TestClient(create_app(state_path)) as c:
        c.post("/api/tokens", json=token("Treasure", 1))
        c.post("/api/tokens", json=token("Treasure", 1))
        c.post("/api/tokens", json=token("Clue", 2))
        before = tokens(c)

    with TestClient(create_app(state_path)) as c:
        assert tokens(c) == before


@pytest.mark.parametrize("content", ["{not json", '{"tokens": [{"x": 1}]}', "[]", ""])
def test_broken_state_file_gives_empty_list(state_path, content):
    state_path.write_text(content, encoding="utf-8")
    with TestClient(create_app(state_path)) as c:
        assert tokens(c) == []


def test_missing_state_file_gives_empty_list(tmp_path):
    with TestClient(create_app(tmp_path / "does-not-exist.json")) as c:
        assert tokens(c) == []


# --- commander tax, storm, spotlight and deck intro --------------------------------------------------


def test_commander_casts_and_storm(client, state_path):
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        client.post("/api/tally/casts")
        assert ws.receive_json()["event"] == {"type": "tally", "name": "casts", "value": 1, "delta": 1}
        assert ws.receive_json()["turn"]["casts"] == 1
    client.post("/api/tally/casts")
    client.post("/api/tally/storm", json={"delta": 3})
    assert turn(client)["casts"] == 2 and turn(client)["storm"] == 3
    client.post("/api/tally/storm", json={"delta": -10})          # never below 0
    assert turn(client)["storm"] == 0
    assert client.post("/api/tally/life").status_code == 422
    client.post("/api/undo")                                        # undo works for tallies too
    assert turn(client)["storm"] == 3
    # Kept after a restart.
    with TestClient(create_app(state_path)) as again:
        assert again.get("/api/state").json()["turn"]["casts"] == 2


def test_storm_resets_on_a_new_turn_and_a_new_game_resets_all(client):
    client.post("/api/tally/casts")
    client.post("/api/tally/storm", json={"delta": 2})
    client.patch("/api/turn", json={"phase": "main2", "on": True})
    client.post("/api/turn/next")
    assert turn(client)["storm"] == 2                               # same turn still
    client.post("/api/turn/next")
    assert turn(client)["storm"] == 0 and turn(client)["casts"] == 1  # new turn: storm starts over
    client.patch("/api/turn", json={"number": 1, "phase": "untap", "casts": 0, "storm": 0})
    assert turn(client)["casts"] == 0


def test_spotlight_is_an_event_only(client):
    card = {"name": "Odric, Lunarch Marshal", "image_url": IMG.format(7), "seconds": 5}
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        assert client.post("/api/spotlight", json=card).status_code == 200
        event = ws.receive_json()["event"]
        assert event["type"] == "spotlight" and event["name"] == card["name"] and event["seconds"] == 5 and event["id"]
        client.delete("/api/spotlight")
        assert ws.receive_json()["event"] == {"type": "spotlight", "id": None}
    assert client.post("/api/spotlight", json={**card, "image_url": "https://evil.example/x.jpg"}).status_code == 422
    assert client.post("/api/spotlight", json={**card, "seconds": 999}).status_code == 422
    assert tokens(client) == []


def test_deck_intro(client):
    assert client.post("/api/intro").status_code == 404             # no deck yet
    intro = {"name": "Odric", "commander": "Odric, Lunarch Marshal", "image_url": "https://evil.example/x.jpg",
             "bracket": "Bracket 2 · Core", "record": "3 W · 1 L"}
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        client.post("/api/intro", json=intro)
        event = ws.receive_json()["event"]
    assert event == {"type": "intro", **intro, "image_url": None}  # only Scryfall pictures


def test_game_result_on_stream_only(client):
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        assert client.post("/api/result", json={"result": "win"}).status_code == 200
        event = ws.receive_json()["event"]
    assert event == {"type": "result", "result": "win", "deck": None, "record": None, "logged": False, "note": None,
                     "tonight": None, "achievements": [], "colors": ""}
    assert client.get("/api/tonight").json() == {"wins": 0, "losses": 0, "draws": 0}
    assert client.post("/api/result", json={"result": "maybe"}).status_code == 422
    assert client.get("/sd/loss").status_code == 200 and client.get("/sd/tracker").status_code == 200
    assert turn(client)["on"]


# --- Stream Deck -----------------------------------------------------------------------------------


def test_streamdeck_keys_fill_an_xl(client):
    data = client.get("/api/streamdeck").json()
    assert len(data["actions"]) + data["preset_keys"] == 32 and data["presets"] == []
    page = client.get("/streamdeck")
    assert page.status_code == 200 and "Stream Deck" in page.text and 'id="downloadProfile"' in page.text
    # Every key works.
    for key in data["actions"]:
        if key["action"] in ("undo", "intro"):
            continue
        assert client.get(f"/sd/{key['action']}").status_code == 200, key


def test_streamdeck_keys_do_their_thing(client):
    client.get("/sd/mana-R")
    client.get("/sd/mana-R")
    client.get("/sd/plus-counter")
    names = sorted((t["name"], t["count"]) for t in tokens(client))
    assert names == [("+1/+1", 1), ("R", 2)]
    client.get("/sd/commander")
    client.get("/sd/commander")
    client.get("/sd/commander-back")
    client.get("/sd/storm")
    assert turn(client)["casts"] == 1 and turn(client)["storm"] == 1
    client.get("/sd/tracker-on")
    client.get("/sd/next-phase")
    assert turn(client)["on"] and turn(client)["phase"] == "upkeep"
    client.get("/sd/new-game")
    assert turn(client) == {"on": True, "number": 1, "phase": "untap", "casts": 0, "storm": 0}
    assert client.get("/sd/undo").status_code == 200
    assert turn(client)["phase"] == "upkeep"
    client.get("/sd/clear")
    assert tokens(client) == []
    assert client.get("/sd/launch-missiles").status_code == 404
    assert client.get("/sd/intro").status_code == 404  # no deck yet


def test_streamdeck_presets(client):
    client.post("/api/tokens", json=token())
    client.post("/api/presets", json={"name": "Krenko Goblins"})
    client.post("/api/clear")
    assert client.get("/api/streamdeck").json()["presets"] == ["Krenko Goblins"]
    assert client.get("/sd/preset/Krenko%20Goblins").status_code == 200
    assert [t["name"] for t in tokens(client)] == ["Treasure"]
    assert client.get("/sd/preset/Nope").status_code == 404


def test_streamdeck_keys_not_from_other_sites(client):
    assert client.get("/sd/mana-G", headers={"Referer": "https://evil.example/page"}).status_code == 403
    assert client.get("/sd/mana-G", headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.get("/sd/preset/x", headers={"Referer": "https://evil.example/"}).status_code == 403
    assert tokens(client) == []
    # This app's own page (the "Try it" button) may.
    assert client.get("/sd/mana-G", headers={"Referer": "http://testserver/streamdeck"}).status_code == 200


def test_mana_symbol_proxy_only_knows_mana(client):
    assert client.get("/api/symbol/X.svg").status_code == 404


# --- part of the MTG Collection Manager -----------------------------------------------------------


def test_mounted_in_the_collection_manager():
    from fastapi.testclient import TestClient as AppClient

    from app.main import app as manager

    with AppClient(manager) as c:
        assert c.get("/stream", follow_redirects=False).headers["location"] == "/stream/control"
        page = c.get("/stream/control")
        assert page.status_code == 200 and "const ROOT = location.pathname" in page.text and 'id="collectionDeckForm"' in page.text
        assert 'id="appNav"' in page.text and '<a href="/deck">Decks</a>' in page.text  # the way back to the app
        assert c.get("/stream/overlay").status_code == 200
        assert c.get("/stream/api/state").json()["tokens"] == []
        with c.websocket_connect("/stream/ws") as ws:
            assert "tokens" in ws.receive_json()
        assert 'href="/stream/control"' in c.get("/").text


def test_deck_tokens_to_the_stream(monkeypatch):
    from fastapi.testclient import TestClient as AppClient

    from app.deps import get_scryfall
    from app.main import app as manager, stream_app
    from app.scryfall import ScryfallClient
    from app.stream import server as stream_server
    from tests.conftest import FakeScryfall

    fake = FakeScryfall()
    manager.dependency_overrides[get_scryfall] = lambda: ScryfallClient(transport=httpx.MockTransport(fake), min_interval=0)
    # The overlay asks Scryfall itself (urllib); here it gets the fake's cards.
    def scryfall_cards(ids):
        return [{**c, "image_uris": {"normal": f"https://cards.scryfall.io/normal/front/a/b/{c['id']}.jpg"}}
                for c in fake.cards if c["id"] in ids]

    monkeypatch.setattr(stream_server, "scryfall_cards", scryfall_cards)
    monkeypatch.setattr(stream_server, "ALLOWED_IMAGE_PREFIX", ("https://cards.scryfall.io/", "https://img.example/"))
    try:
        with AppClient(manager) as c:
            deck = c.post("/api/decks", json={"name": "Tokens", "decklist": "1 Pitiless Plunderer\n1 Raise the Alarm"}).json()
            c.get(f"/decks/{deck['id']}")  # looks the cards up
            found = c.post(f"/api/decks/{deck['id']}/stream").json()
            assert found["source"] == "collection" and found["name"] == "Tokens" and found["cards"] == 2
            names = {t["name"]: t for t in found["tokens"]}
            assert set(names) == {"Soldier", "Treasure"}
            assert names["Treasure"]["made_by"] == ["Pitiless Plunderer"]
            assert names["Soldier"]["image_url"] == "https://cards.scryfall.io/normal/front/a/b/id-soldier.jpg"
            assert found["intro"] == {"name": "Tokens", "commander": None, "image_url": None, "bracket": None, "record": None, "colors": "WB"}
            assert [c["name"] for c in found["spotlight"]] == ["Pitiless Plunderer", "Raise the Alarm"]
            # The control panel shows it like a scanned deck.
            assert c.get("/stream/api/deck").json()["deck_id"] == deck["id"]
            # The button on the deck page sends you to the control panel.
            c.post("/api/games", json={"deck_id": deck["id"], "result": "win"})
            with c.websocket_connect("/stream/ws") as ws:
                ws.receive_json()
                r = c.post(f"/decks/{deck['id']}/stream", follow_redirects=False)
                assert ws.receive_json()["event"] == {"type": "deck"}
                intro = ws.receive_json()["event"]  # Play also shows the deck intro
                assert intro["type"] == "intro" and intro["name"] == "Tokens" and intro["record"] == "1 W · 0 L"
                again = c.post(f"/api/decks/{deck['id']}/stream/intro").json()
                assert again == intro and ws.receive_json()["event"] == intro
            assert r.status_code == 303 and r.headers["location"] == "/stream/control#add"
            assert c.post("/api/decks/99999/stream/intro").status_code == 404
            page = c.get(f"/decks/{deck['id']}").text
            # Play opens the stream screen with the deck's tokens; the life counter has the deck chosen.
            assert f'action="/decks/{deck['id']}/stream"' in page and "▶ Play" in page and f'href="/table?deck={deck['id']}"' in page
            assert f'value="{deck["id"]}" data-commander="" selected' in c.get(f"/table?deck={deck['id']}").text
    finally:
        manager.dependency_overrides.clear()
        stream_app.state.decks.save(None)


def test_victory_logs_the_game():
    from fastapi.testclient import TestClient as AppClient

    from app.deps import get_scryfall
    from app.main import app as manager, stream_app
    from app.scryfall import ScryfallClient
    from tests.conftest import FakeScryfall

    manager.dependency_overrides[get_scryfall] = lambda: ScryfallClient(transport=httpx.MockTransport(FakeScryfall()), min_interval=0)
    try:
        with AppClient(manager) as client, TestClient(stream_app) as stream:
            deck = client.post("/api/decks", json={"name": "Ramp", "decklist": "1 Sol Ring"}).json()
            assert stream.post("/api/result", json={"result": "win"}).json()["note"].startswith("Pick one of your saved decks")
            stream_app.state.decks.save({"source": "collection", "deck_id": deck["id"], "name": "Ramp", "tokens": []})
            first = stream.post("/api/result", json={"result": "win"}).json()
            assert first["logged"] and first["deck"] == "Ramp" and first["record"] == "1 W · 0 L"
            assert first["tonight"] == {"wins": 1, "losses": 0, "draws": 0}
            assert [a["title"] for a in first["achievements"]] == ["First blood", "First win"]
            assert stream.get("/api/tonight").json()["wins"] == 1
            again = stream.get("/sd/win").json()  # a double press on the Stream Deck
            assert again["ok"]
            assert stream.post("/api/result", json={"result": "win"}).json()["note"] == "Already logged a moment ago"
            assert stream.post("/api/result", json={"result": "loss", "log": False}).json()["logged"] is False
            games = client.get("/api/games").json()
            assert [(g["deck_name"], g["result"]) for g in games] == [("Ramp", "win")]
    finally:
        manager.dependency_overrides.clear()
        stream_app.state.decks.save(None)


# --- two tokens on one picture (the Roles of Wilds of Eldraine) -------------------------------


ROLES = {
    "id": "role", "name": "Monster // Sorcerer", "layout": "flip", "keywords": ["Trample"],
    "image_uris": {"normal": IMG.format("role")}, "type_line": "Token Enchantment — Aura Role",
    "card_faces": [
        {"name": "Monster", "type_line": "Token Enchantment — Aura Role",
         "oracle_text": "Enchant creature\nEnchanted creature gets +1/+1 and has trample."},
        {"name": "Sorcerer", "type_line": "Token Enchantment — Aura Role",
         "oracle_text": "Enchant creature\nEnchanted creature gets +1/+1 and has \"Whenever this creature attacks, scry 1.\""},
    ],
}


def test_a_flip_picture_is_two_tokens():
    from app.stream.server import token_infos

    top, bottom = token_infos(ROLES)
    assert (top["name"], top["part"], bottom["name"], bottom["part"]) == ("Monster", "top", "Sorcerer", "bottom")
    assert top["keywords"] == ["Trample"] and bottom["keywords"] == []
    assert top["image_url"] == bottom["image_url"] == IMG.format("role")
    [plain] = token_infos({"id": "t", "name": "Treasure", "image_uris": {"normal": IMG.format("t")}})
    assert plain["part"] == "whole"


def test_the_parts_of_a_picture_are_separate_stacks(client):
    monster = {"name": "Monster", "image_url": IMG.format("role"), "part": "top"}
    sorcerer = {"name": "Sorcerer", "image_url": IMG.format("role"), "part": "bottom"}
    for item in (monster, monster, sorcerer):
        assert client.post("/api/tokens", json=item).status_code == 201
    by_name = {t["name"]: t for t in tokens(client)}
    assert (by_name["Monster"]["count"], by_name["Monster"]["part"]) == (2, "top")
    assert (by_name["Sorcerer"]["count"], by_name["Sorcerer"]["part"]) == (1, "bottom")
    assert client.post("/api/tokens", json={**monster, "part": "middle"}).status_code == 422
    assert tokens(client)[0]["part"] in ("top", "bottom")


def test_the_ring_emblem_is_a_name_bar(client):
    from app.stream.server import token_infos

    ring = {"id": "ring", "name": "The Ring // The Ring Tempts You", "layout": "double_faced_token",
            "card_faces": [{"name": "The Ring", "type_line": "Emblem", "image_uris": {"normal": IMG.format("ring")},
                            "oracle_text": "Your Ring-bearer is legendary."},
                           {"name": "The Ring Tempts You", "type_line": "Card", "image_uris": {"normal": IMG.format("ring-back")}}]}
    [info] = token_infos(ring)
    assert info["name"] == "The Ring" and info["part"] == "whole" and info["bar"] == 0.44
    assert "bar" not in token_infos({"id": "t", "name": "Treasure", "image_uris": {"normal": IMG.format("t")}})[0]

    assert client.post("/api/tokens", json={"name": "The Ring", "image_url": IMG.format("ring"), "bar": 0.44}).status_code == 201
    [t] = tokens(client)
    assert t["bar"] == 0.44 and t["part"] == "whole"
    assert client.post("/api/tokens", json={"name": "Bad", "image_url": IMG.format("x"), "bar": 3}).status_code == 422


def test_name_bar_or_whole_token(client):
    ring = {"name": "The Ring", "image_url": IMG.format("ring"), "bar": 0.44}
    monster = {"name": "Monster", "image_url": IMG.format("role"), "part": "top"}
    client.post("/api/tokens", json=ring)
    client.post("/api/tokens", json=monster)
    assert all(t["full"] is False for t in tokens(client))  # the name bar is the default
    [role] = [t for t in tokens(client) if t["name"] == "Monster"]
    assert client.patch(f"/api/tokens/{role['id']}", json={"full": True}).status_code == 200
    assert {t["name"]: t["full"] for t in tokens(client)} == {"The Ring": False, "Monster": True}
    # Another Monster joins the stack and the stack keeps its look.
    client.post("/api/tokens", json=monster)
    assert sorted((t["name"], t["count"], t["full"]) for t in tokens(client)) == [
        ("Monster", 2, True), ("The Ring", 1, False)]
    # A copy of a stack looks like it, and a split-off part too.
    client.post(f"/api/tokens/{role['id']}/split", json={"count": 1})
    assert sorted((t["name"], t["count"], t["full"]) for t in tokens(client)) == [
        ("Monster", 1, True), ("Monster", 1, True), ("The Ring", 1, False)]
    assert client.patch(f"/api/tokens/{role['id']}", json={"full": False}).status_code == 200
    assert {t["id"]: t["full"] for t in tokens(client)}[role["id"]] is False


def test_on_an_adventure_is_a_token_without_name_plate():
    from app.stream.server import token_infos

    helper = {"id": "adv", "name": "On an Adventure", "layout": "token", "type_line": "Card",
              "image_uris": {"normal": IMG.format("adv")}}
    [info] = token_infos(helper)
    assert info["name"] == "On an Adventure" and info["plate"] is False and "bar" not in info
    [plain] = token_infos({"id": "t", "name": "Treasure", "image_uris": {"normal": IMG.format("t")}})
    assert plain["plate"] is True


def test_deck_tokens_to_the_stream_without_visiting_the_deck_first(monkeypatch):
    """The cards of a deck Scryfall was not asked about yet are looked up when you send it to the stream."""
    from fastapi.testclient import TestClient as AppClient

    from app.deps import get_scryfall
    from app.main import app as manager
    from app.scryfall import ScryfallClient
    from app.stream import server as stream_server
    from tests.conftest import FakeScryfall

    fake = FakeScryfall()
    manager.dependency_overrides[get_scryfall] = lambda: ScryfallClient(transport=httpx.MockTransport(fake), min_interval=0)
    monkeypatch.setattr(stream_server, "scryfall_cards", lambda ids: [
        {**c, "image_uris": {"normal": f"https://cards.scryfall.io/normal/front/a/b/{c['id']}.jpg"}} for c in fake.cards if c["id"] in ids])
    try:
        with AppClient(manager) as c:
            deck = c.post("/api/decks", json={"name": "Tokens", "decklist": "1 Pitiless Plunderer\n1 Raise the Alarm"}).json()
            found = c.post(f"/api/decks/{deck['id']}/stream").json()  # the deck page was never opened
            assert {t["name"] for t in found["tokens"]} == {"Soldier", "Treasure"}
    finally:
        manager.dependency_overrides.clear()


def test_scanning_a_deck_link_finds_emblems_and_helper_cards(client, fake_net, monkeypatch):
    monkeypatch.setitem(SCRYFALL["pitiless"], "all_parts", [
        {"component": "token", "id": "treasure"},
        {"component": "combo_piece", "id": "adv", "name": "On an Adventure", "type_line": "Card"},
        {"component": "combo_piece", "id": "ring", "name": "The Ring", "type_line": "Emblem // Card"},
        {"component": "combo_piece", "id": "pitiless", "name": "Pitiless Plunderer", "type_line": "Creature — Human"},
    ])
    monkeypatch.setitem(SCRYFALL, "adv", {"id": "adv", "name": "On an Adventure", "keywords": [], "layout": "token",
                                          "type_line": "Card", "oracle_text": "After an Adventure resolves...",
                                          "image_uris": {"normal": IMG_A}})
    monkeypatch.setitem(SCRYFALL, "ring", {"id": "ring", "name": "The Ring // The Ring Tempts You", "keywords": [],
                                           "layout": "double_faced_token", "type_line": "Emblem // Card",
                                           "card_faces": [{"name": "The Ring", "type_line": "Emblem", "image_uris": {"normal": IMG_B}},
                                                          {"name": "The Ring Tempts You", "type_line": "Card", "image_uris": {"normal": IMG_A}}]})
    deck = client.post("/api/deck", json={"url": "https://archidekt.com/decks/1234/krenko_goblins"}).json()
    names = {t["name"]: t for t in deck["tokens"]}
    assert {"On an Adventure", "The Ring", "Treasure"} <= set(names)
    assert names["On an Adventure"]["plate"] is False and names["The Ring"]["bar"] == 0.44


def test_the_version_tells_the_control_panel_when_its_page_changed(client):
    info = client.get("/api/version").json()
    assert info["outdated"] is False and len(info["page"]) == 10
    assert client.get("/api/version").json()["page"] == info["page"]
