"""Salt score, the mana base check, and how games were won."""

import asyncio

import pytest

from app.models import Deck, DeckBasicLand, DeckEntry
from app.services import deck_insights, games, mana_base


# --- mana base ----------------------------------------------------------------------------


def test_cost_parts_reads_symbols_per_face():
    assert mana_base.cost_parts("{1}{U}{U}") == [("{1}{U}{U}", 3, {"U": 2})]
    assert mana_base.cost_parts("{R} // {2}{G/U}{W}") == [("{R}", 1, {"R": 1}), ("{2}{G/U}{W}", 4, {"W": 1})]
    assert mana_base.cost_parts("{X}{B}") == [("{X}{B}", 1, {"B": 1})]
    assert mana_base.cost_parts("") == [] and mana_base.cost_parts(None) == []


def test_sources_needed_follows_karsten():
    # About what his tables say for a 99 card deck: a one-drop wants ~19-20 sources, a heavy cost many more.
    assert 17 <= mana_base.sources_needed(1, 2) <= 21
    assert mana_base.sources_needed(2, 3) > mana_base.sources_needed(1, 3)
    assert mana_base.sources_needed(2, 3) > mana_base.sources_needed(2, 5)
    assert mana_base.chance_at_least(99, 37, 7, 0) == 1.0
    assert 0.75 < mana_base.chance_at_least(99, 37, 10, 3) < 0.85


def _entry(session, deck, name, type_line, cost="", produces="", cmc=0.0, quantity=1):
    session.add(DeckEntry(deck_id=deck.id, name=name, quantity=quantity, type_line=type_line, mana_cost=cost,
                          produces=produces, cmc=cmc, colors="", bracket_flags="", token_parts="[]", roles=""))


def test_mana_base_counts_sources_per_colour(session):
    deck = Deck(name="Dimir", colors="UB")
    session.add(deck)
    session.flush()
    session.add(DeckBasicLand(deck_id=deck.id, name="Island", quantity=8))
    session.add(DeckBasicLand(deck_id=deck.id, name="Swamp", quantity=2))
    _entry(session, deck, "Watery Tomb", "Land", produces="UB", quantity=3)
    _entry(session, deck, "Evolving Wilds", "Land", produces="F")
    _entry(session, deck, "Arcane Signet", "Artifact", "{2}", "UB", 2.0)
    _entry(session, deck, "Counterspell", "Instant", "{U}{U}", cmc=2.0, quantity=1)
    _entry(session, deck, "Doom Blade", "Instant", "{1}{B}", cmc=2.0)
    session.commit()

    base = mana_base.mana_base(session, deck)
    by_colour = {c.colour: c for c in base.colours}
    assert (base.lands, base.rocks) == (14, 1)
    assert by_colour["U"].sources == 8 + 3 + 1 + 1  # basics, duals, the fetch land and the Signet
    assert by_colour["B"].sources == 2 + 3 + 1 + 1
    assert by_colour["U"].hardest == "Counterspell" and by_colour["U"].symbols == 2
    assert by_colour["B"].status == "short" and by_colour["B"].needed > by_colour["B"].sources
    assert any("Black" in line for line in base.advice)
    assert base.land_drops and base.land_drops[0][0].startswith("2 lands")


def test_deck_page_shows_mana_base_and_salt(client, session):
    deck = client.post("/api/decks", json={"name": "Salty", "decklist": "1 Sol Ring\n1 Marwyn, the Nurturer"}).json()
    page = client.get(f"/decks/{deck['id']}").text
    assert "Mana base" in page or "Salt score" in page
    assert "Salt score" in page and "Sol Ring" in page  # the fake EDHREC rates Sol Ring salty


# --- salt ---------------------------------------------------------------------------------


def test_salt_scores_are_fetched_once(edhrec, fake_edhrec):
    scores = asyncio.run(edhrec.salt_scores())
    assert scores == {"sol ring": 1.5, "rhystic study": 1.2}
    asyncio.run(edhrec.salt_scores())
    assert len([r for r in fake_edhrec.requests if "salt" in r.url.path]) == 1


def test_salt_check_adds_up_the_salty_cards(session):
    deck = Deck(name="Salty")
    session.add(deck)
    session.flush()
    for name in ("Rhystic Study", "Sol Ring", "Forest"):
        _entry(session, deck, name, "Artifact")
    session.commit()
    check = deck_insights.salt_check(session, deck, {"rhystic study": 1.2, "sol ring": 1.5})
    assert check.total == 2.7 and [name for name, _ in check.cards] == ["Sol Ring", "Rhystic Study"]
    assert check.level[0] == "A pinch of salt"
    assert deck_insights.salt_check(session, deck, {}).level[0] == "Sweet"


# --- how games are won -------------------------------------------------------------------


def test_win_methods_and_turn_order_in_the_stats(session):
    deck = Deck(name="Kess")
    session.add(deck)
    session.flush()
    games.log_game(session, deck, "win", players=4, win_method="combo", went_first=True)
    games.log_game(session, deck, "win", players=4, win_method="combo", went_first=False)
    games.log_game(session, deck, "win", players=4, win_method="combat", went_first=True)
    games.log_game(session, deck, "loss", players=4, opponents="Sam", winner="Sam", win_method="combo", went_first=False)
    games.log_game(session, deck, "draw", players=4, win_method="combo")  # a draw has no winning move
    games.log_game(session, deck, "win", players=4)
    stats = games.log_stats(session)
    methods = {t.label: t for t in stats.win_methods}
    assert (methods["combo"].played, methods["combo"].wins, methods["combo"].their_wins) == (3, 2, 1)
    assert methods["combat"].wins == 1
    assert (stats.first.played, stats.first.wins) == (2, 2) and (stats.not_first.played, stats.not_first.wins) == (2, 1)
    assert games.deck_plays(session)[deck.id].favourite_win == ("Combo", 2)
    with pytest.raises(games.GameError):
        games.log_game(session, deck, "win", win_method="magic")


def test_game_details_can_be_filled_in_afterwards(client):
    deck = client.post("/api/decks", json={"name": "Kess", "decklist": "1 Sol Ring"}).json()
    game = client.post("/api/games", json={"deck_id": deck["id"], "result": "win"}).json()
    assert game["win_method"] is None and game["went_first"] is None
    changed = client.patch(f"/api/games/{game['id']}", json={"win_method": "commander", "went_first": True}).json()
    assert changed["win_method"] == "commander" and changed["went_first"] is True
    assert client.patch(f"/api/games/{game['id']}", json={"win_method": "nope"}).status_code == 400
    assert client.patch("/api/games/999", json={}).status_code == 404
    page = client.get("/games").text
    assert "Commander damage" in page and "How games are won" in page
    client.post(f"/games/{game['id']}/details", data={"win_method": "combo", "went_first": "other"})
    assert client.get("/api/games").json()[0]["went_first"] is False


# --- the database of before ---------------------------------------------------------------


def test_new_columns_reach_a_database_from_before():
    """The mana cost, what a card produces and how a game was won are added to the tables of an older database."""
    from sqlalchemy import create_engine

    from app.database import add_missing_columns

    old = create_engine("sqlite://")
    with old.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE deckentry (id INTEGER PRIMARY KEY, deck_id INTEGER, name VARCHAR, quantity INTEGER)")
        connection.exec_driver_sql("CREATE TABLE game (id INTEGER PRIMARY KEY, played_on DATE, deck_name VARCHAR, result VARCHAR)")
        connection.exec_driver_sql("CREATE TABLE decktoken (id INTEGER PRIMARY KEY, deck_id INTEGER, name VARCHAR, quantity INTEGER, scryfall_id VARCHAR, image_url VARCHAR)")
        connection.exec_driver_sql("INSERT INTO deckentry (deck_id, name, quantity) VALUES (1, 'Sol Ring', 1)")
        connection.exec_driver_sql("INSERT INTO game (played_on, deck_name, result) VALUES ('2026-10-01', 'Kess', 'win')")
    add_missing_columns(old)
    with old.begin() as connection:
        assert connection.exec_driver_sql("SELECT name, mana_cost, produces FROM deckentry").all() == [("Sol Ring", None, None)]
        assert connection.exec_driver_sql("SELECT result, win_method, went_first FROM game").all() == [("win", None, None)]
        assert connection.exec_driver_sql("SELECT back_image_url FROM decktoken").all() == []


# --- tokens with two sides ----------------------------------------------------------------


def test_both_sides_of_a_double_faced_token(client, session):
    from app.scryfall import ScryfallCard
    from app.services.decks import deck_tokens

    deck = client.post("/api/decks", json={"name": "Wolves", "decklist": "1 Sol Ring"}).json()
    client.post(f"/decks/{deck['id']}/tokens", data={"name": "Wolf // Rabbit", "quantity": "2"})
    client.post(f"/decks/{deck['id']}/tokens", data={"name": "Cat Beast", "quantity": "1"})
    page = client.get(f"/decks/{deck['id']}").text
    assert "https://img.example/wolf-front.jpg" in page and "https://img.example/wolf-back.jpg" in page
    assert "two-sided" in page and 'id="tokens-top"' in page
    tokens = {t.name: t for t in deck_tokens(session, session.get(Deck, deck["id"]))}
    assert tokens["Wolf // Rabbit"].back_image_url == "https://img.example/wolf-back.jpg"
    assert tokens["Cat Beast"].back_image_url == ""  # one side

    sheet = client.get(f"/decks/{deck['id']}/tokens/print").text
    assert sheet.count("wolf-front.jpg") == 2 and sheet.count("wolf-back.jpg") == 2  # two of each side
    assert "id-cat-beast" in sheet

    card = ScryfallCard.from_json({"id": "x", "name": "Plain", "image_uris": {"normal": "front"},
                                   "card_faces": [{"image_uris": {"normal": "a"}}, {"image_uris": {"normal": "b"}}]})
    assert card.back_image_url is None  # a card with one picture for both faces has no other side


def test_two_tokens_on_one_picture_are_split(client, session):
    """The Roles of Wilds of Eldraine: Monster on top, Sorcerer upside down below it, on one picture."""
    from app.services.decks import deck_tokens

    deck = client.post("/api/decks", json={"name": "Roles", "decklist": "1 Sol Ring"}).json()
    client.post(f"/decks/{deck['id']}/tokens", data={"name": "Monster // Sorcerer", "quantity": "2"})
    page = client.get(f"/decks/{deck['id']}").text
    assert 'class="half top"' in page and 'class="half bottom"' in page
    assert 'title="Monster"' in page and 'title="Sorcerer"' in page
    [token] = deck_tokens(session, session.get(Deck, deck["id"]))
    assert token.flip is True and token.back_image_url == ""

    both = client.get(f"/decks/{deck['id']}/tokens/print").text
    assert both.count("half top") == 2 and both.count("half bottom") == 2  # two of each, cut in two
    only_top = client.get(f"/decks/{deck['id']}/tokens/print?top=true").text
    assert only_top.count("half top") == 2 and "half bottom" not in only_top


def test_helper_cards_like_on_an_adventure_count_as_tokens(client, session):
    """Scryfall lists "On an Adventure" as a combo piece of type "Card" on every Adventure; you need one."""
    from app.scryfall import ScryfallCard
    from app.services.deck_insights import _token_parts, one_enough

    data = ScryfallCard.from_json({
        "id": "bone", "name": "Bonecrusher Giant // Stomp", "oracle_text": "Stomp deals 2 damage to any target.",
        "all_parts": [{"component": "combo_piece", "id": "adv", "name": "On an Adventure", "type_line": "Card"},
                      {"component": "combo_piece", "id": "bone", "name": "Bonecrusher Giant // Stomp", "type_line": "Creature — Giant"}],
    })
    assert data.token_parts == [("adv", "On an Adventure")]
    assert __import__("json").loads(_token_parts(data)) == [{"id": "adv", "name": "On an Adventure", "count": 1}]
    assert one_enough("On an Adventure") and one_enough("The Ring") and one_enough("Elspeth Emblem") and not one_enough("Treasure")
