"""Bracket check, game statistics, purchase prices and price drops on the wishlist."""

import datetime as dt

from sqlmodel import select

from app.csv_import import parse_collection_csv
from app.jobs import check_price_drops
from app.models import Card, CollectionEntry, Deck, PriceDropAlert
from app.services import deck_insights
from app.services.games import log_game, log_stats
from app.services.pricing import SCRYFALL, record_snapshot

TODAY = dt.date.today()


# --- bracket check ------------------------------------------------------------------------


def test_bracket_flags_from_rules_text():
    assert deck_insights.bracket_flags("Destroy all lands.") == "mld"
    assert deck_insights.bracket_flags("Destroy all artifacts, creatures, and lands.") == "mld"
    assert deck_insights.bracket_flags("Each player sacrifices half the lands they control.") == "mld"
    assert deck_insights.bracket_flags("Lands don't untap during their controllers' untap steps.") == "mld"
    assert deck_insights.bracket_flags("Target player takes an extra turn after this one.") == "extraturn"
    assert deck_insights.bracket_flags("Search your library for a basic land card.") == ""
    assert deck_insights.bracket_flags("Destroy target land.") == ""


def test_bracket_check(client):
    deck = client.post("/api/decks", json={
        "name": "Blue", "decklist": "1 Rhystic Study\n1 Armageddon\n1 Time Warp\n1 Sol Ring", "bracket": 2,
    }).json()
    check = client.get(f"/api/decks/{deck['id']}/bracket-check").json()
    assert check["game_changers"] == ["Rhystic Study"]
    assert check["mass_land_denial"] == ["Armageddon"]
    assert check["extra_turns"] == ["Time Warp"]
    assert check["fits"] is False and check["lowest_bracket"] == 4
    assert any("1 Game Changer" in problem for problem in check["problems"])

    page = client.get(f"/decks/{deck['id']}").text
    assert "Bracket check" in page and "Does not fit bracket 2" in page and 'class="badge changer">Rhystic Study' in page
    assert 'class="badge warn"' in client.get("/deck").text  # the list of decks warns as well

    # In bracket 4 anything goes.
    client.put(f"/api/decks/{deck['id']}/bracket", json={"bracket": 4})
    assert client.get(f"/api/decks/{deck['id']}/bracket-check").json()["fits"] is True
    assert "Fits bracket 4" in client.get(f"/decks/{deck['id']}").text


def test_bracket_three_allows_three_game_changers(session):
    deck = Deck(name="Three", bracket=3)
    session.add(deck)
    session.flush()
    check = deck_insights.BracketCheck(bracket=3, game_changers=["A", "B", "C"])
    assert check.fits and check.minimum == 3
    check.game_changers.append("D")
    assert not check.fits and check.minimum == 4
    assert deck_insights.BracketCheck(bracket=None).minimum == 1
    # Without the Game Changers list the rest is still checked.
    assert deck_insights.bracket_check(session, deck, None).known is False


# --- game statistics --------------------------------------------------------------------------


def test_opponents_and_winner(client, session):
    ramp = client.post("/api/decks", json={"name": "Ramp", "decklist": "1 Sol Ring"}).json()
    game = client.post("/api/games", json={
        "deck_id": ramp["id"], "result": "loss", "players": 4, "opponents": ["Sam", " robin", "sam"], "winner": "ROBIN",
    }).json()
    assert game["opponents"] == "Sam, robin" and game["winner"] == "robin"
    # Someone who won is at the table too; a winner of a won game is ignored.
    game = client.post("/api/games", json={"deck_id": ramp["id"], "result": "loss", "opponents": ["Sam"], "winner": "Alex"}).json()
    assert game["opponents"] == "Sam, Alex"
    game = client.post("/api/games", json={"deck_id": ramp["id"], "result": "win", "opponents": ["Sam"], "winner": "Sam"}).json()
    assert game["winner"] is None
    too_many = client.post("/api/games", json={"deck_id": ramp["id"], "result": "win", "players": 2, "opponents": ["A", "B"]})
    assert too_many.status_code == 400

    # The page form takes the names separated by commas.
    client.post("/games", data={"deck_id": ramp["id"], "result": "loss", "opponents": "Sam, Robin", "winner": "Sam"})
    page = client.get("/games").text
    assert "Sam, Robin" in page and "Sam won" in page and 'data-player="Sam"' in page


def test_log_stats(session):
    mono_white = Deck(name="White", colors="W", bracket=2)
    azorius = Deck(name="Azorius", colors="WU", bracket=3)
    session.add_all([mono_white, azorius])
    session.flush()
    month_ago = TODAY - dt.timedelta(days=40)
    log_game(session, mono_white, "loss", month_ago, 4, opponents="Sam, Robin", winner="Sam")
    log_game(session, azorius, "loss", TODAY - dt.timedelta(days=3), 4, opponents="Sam", winner="Sam")
    log_game(session, azorius, "win", TODAY - dt.timedelta(days=2), 3, opponents="Robin")
    log_game(session, mono_white, "win", TODAY - dt.timedelta(days=1), 4, opponents="Sam, Robin")
    log_game(session, azorius, "win", TODAY, 4, opponents="robin")

    stats = log_stats(session)
    assert (stats.played, stats.wins, stats.losses, stats.win_rate) == (5, 3, 2, 60)
    assert stats.streak == ("win", 3) and stats.longest_win_streak == 3
    assert [(t.label, t.played, t.wins) for t in stats.table_sizes] == [("3", 1, 1), ("4", 4, 2)]
    assert [(t.label, t.played, t.wins) for t in stats.colours] == [("W", 5, 3), ("U", 3, 2)]
    assert [(t.label, t.played) for t in stats.brackets] == [("2", 2), ("3", 3)]
    assert [t.label for t in stats.opponents] == ["Robin", "Sam"]  # most games together first
    by_name = {t.label: t for t in stats.opponents}
    assert (by_name["Robin"].played, by_name["Robin"].wins, by_name["Robin"].their_wins) == (4, 3, 0)
    assert (by_name["Sam"].played, by_name["Sam"].wins, by_name["Sam"].their_wins) == (3, 1, 2)
    assert stats.nemesis.label == "Sam"
    assert stats.months[-1].label == TODAY.strftime("%Y-%m") and sum(t.played for t in stats.months) == 5
    assert len(stats.months) <= 12


def test_games_page_shows_statistics(client):
    ramp = client.post("/api/decks", json={"name": "Ramp", "decklist": "1 Sol Ring"}).json()
    assert "Games per month" not in client.get("/games").text
    client.post("/api/games", json={"deck_id": ramp["id"], "result": "win", "players": 4, "opponents": ["Sam"]})
    page = client.get("/games").text
    assert "Games per month" in page and "By table size" in page and "Opponents" in page and "25% with 4 players" in page
    overview = client.get("/api/games/overview").json()
    assert overview["played"] == 1 and overview["streak"] == {"result": "win", "games": 1}
    assert overview["opponents"] == [{"name": "Sam", "played": 1, "your_wins": 1, "their_wins": 0, "commanders": []}]


# --- purchase price ------------------------------------------------------------------------


def test_purchase_price(client, session):
    added = client.post("/api/collection", json={"name": "Lim-Dûl's Vault", "quantity": 2, "purchase_price": 10}).json()
    assert added["purchase_price"] == 10 and added["paid_total"] == 20 and added["gain"] == 4.68  # 2 x 12.34 now
    # Two more for €4: the average is kept.
    again = client.post("/api/collection", json={"name": "Lim-Dûl's Vault", "quantity": 2, "purchase_price": 4}).json()
    assert again["purchase_price"] == 7.0
    changed = client.patch(f"/api/collection/{added['id']}", json={"purchase_price": 12.5}).json()
    assert changed["purchase_price"] == 12.5 and changed["quantity"] == 4
    cleared = client.patch(f"/api/collection/{added['id']}", json={"purchase_price": None}).json()
    assert cleared["purchase_price"] is None and cleared["gain"] is None

    client.patch(f"/api/collection/{added['id']}", json={"purchase_price": 10})
    page = client.get("/collection").text
    assert "You paid <strong>€40.00</strong>" in page and 'class="paid"' in page
    assert "Paid €40.00 for 4 of them" in client.get("/").text
    # The page form without JavaScript.
    client.post(f"/collection/{added['id']}/paid", data={"purchase_price": "11,50"})
    assert session.get(CollectionEntry, added["id"]).purchase_price == 11.5


def test_purchase_price_from_csv_and_in_the_export(client, session):
    parsed = parse_collection_csv(
        "Count,Name,Edition,Foil,Purchase Price\n2,Sol Ring,cmm,,€1,25\n1,Lim-Dûl's Vault,all,,\n".replace("€1,25", '"€1,25"')
    )
    assert [row.purchase_price for row in parsed.rows] == [1.25, None]
    client.post("/api/collection/import", files={"file": ("c.csv", "Count,Name,Edition,Purchase Price\n2,Sol Ring,cmm,1.25\n")})
    entry = session.exec(select(CollectionEntry)).one()
    assert entry.purchase_price == 1.25
    assert '"1.25"' in client.get("/collection/export.csv").text


def test_bought_from_the_wishlist_with_a_price(client, session):
    wish = client.post("/api/wishlist", json={"name": "Sol Ring", "set_code": "cmm", "target_price": 2}).json()
    client.post(f"/wishlist/{wish['id']}/purchase", data={"paid": "1,80"})
    assert session.exec(select(CollectionEntry)).one().purchase_price == 1.8
    wish = client.post("/api/wishlist", json={"name": "Lim-Dûl's Vault", "target_price": 10}).json()
    bought = client.post(f"/api/wishlist/{wish['id']}/purchase", json={"price": 9.5}).json()
    assert bought["purchase_price"] == 9.5


# --- price drops on the wishlist --------------------------------------------------------------


class FakeNotifier:
    configured = True

    def __init__(self):
        self.sent = []

    async def send_price_drops(self, lines, days):
        self.sent.append((lines, days))
        return True


async def test_price_drop_alerts(client, session):
    vault_wish = client.post("/api/wishlist", json={"name": "Lim-Dûl's Vault", "target_price": 10}).json()  # €12.34 now
    client.post("/api/wishlist", json={"name": "Sol Ring", "set_code": "cmm", "target_price": 1}).json()  # €1.50 now
    vault = session.exec(select(Card).where(Card.scryfall_id == "id-vault")).one()
    sol = session.exec(select(Card).where(Card.scryfall_id == "id-sol")).one()
    week_ago = TODAY - dt.timedelta(days=6)
    record_snapshot(session, vault.id, SCRYFALL, 16.0, day=week_ago)  # -23%
    record_snapshot(session, sol.id, SCRYFALL, 1.60, day=week_ago)  # -6%, too little
    session.commit()

    notifier = FakeNotifier()
    alerts = await check_price_drops(session, notifier, min_pct=15, min_eur=0.5, days=7)
    assert [(a.wishlist_entry_id, a.old_price, a.new_price, a.notified) for a in alerts] == [(vault_wish["id"], 16.0, 12.34, True)]
    lines, days = notifier.sent[0]
    assert days == 7 and "Lim-Dûl's Vault" in lines[0] and "€16.00 → €12.34" in lines[0] and "your target €10.00" in lines[0]

    # Not again within the period, and nothing when switched off.
    assert await check_price_drops(session, notifier, min_pct=15, min_eur=0.5, days=7) == []
    assert await check_price_drops(session, notifier, min_pct=0, min_eur=0.5, days=7) == []
    assert len(session.exec(select(PriceDropAlert)).all()) == 1
    assert "data-tab=\"drops\"" in client.get("/").text and "sent to Discord" in client.get("/").text
    assert "% cheaper" in client.get("/wishlist").text


def test_a_price_for_every_copy(client):
    # Four copies bought apart: every copy keeps its own price.
    first = client.post("/api/collection", json={"name": "Sol Ring", "purchase_price": 1.00}).json()
    for price in (2.00, 3.00):
        client.post("/api/collection", json={"name": "Sol Ring", "purchase_price": price})
    item = client.post("/api/collection", json={"name": "Sol Ring"}).json()  # the fourth: price not known
    assert item["id"] == first["id"] and item["quantity"] == 4
    assert item["purchase_prices"] == [1.0, 2.0, 3.0] and item["purchase_price"] == 2.0
    unit = item["unit_price"]
    assert item["paid_total"] == 6.0 and item["gain"] == round(unit * 3 - 6.0, 2)  # only the copies with a price

    # Fill them in per copy (the last one too).
    saved = client.patch(f"/api/collection/{item['id']}", json={"purchase_prices": [1.0, 2.0, 3.0, 4.5]}).json()
    assert saved["purchase_prices"] == [1.0, 2.0, 3.0, 4.5] and saved["purchase_price"] == 2.62 and saved["paid_total"] == 10.5
    assert client.patch(f"/api/collection/{item['id']}", json={"purchase_prices": [1, 2, 3, 4, 5]}).status_code == 400
    assert client.patch(f"/api/collection/{item['id']}", json={"purchase_prices": [-1]}).status_code == 400

    page = client.get("/collection").text
    assert 'class="paid varies"' in page and "avg of 4" in page and "Average of 4: €1.00, €2.00, €3.00, €4.50" in page

    # One copy less: the price of the last one goes too.
    fewer = client.patch(f"/api/collection/{item['id']}", json={"quantity": 3}).json()
    assert fewer["purchase_prices"] == [1.0, 2.0, 3.0]
    # One price for all: the list is gone.
    one = client.patch(f"/api/collection/{item['id']}", json={"purchase_price": 2.5}).json()
    assert one["purchase_prices"] == [2.5, 2.5, 2.5] and one["paid_total"] == 7.5
    assert 'class="paid varies"' not in client.get("/collection").text


def test_cardmarket_links_without_tracking_extras():
    import sqlite3
    import tempfile
    from pathlib import Path

    from sqlalchemy import create_engine

    from app.cardmarket import clean_cardmarket_url
    from app.database import run_data_migrations
    from app.scryfall import ScryfallCard

    scryfall_link = ("https://www.cardmarket.com/en/Magic/Products?idProduct=292863&referrer=scryfall"
                     "&utm_campaign=card_prices&utm_medium=text&utm_source=scryfall")
    assert clean_cardmarket_url(scryfall_link) == "https://www.cardmarket.com/en/Magic/Products?idProduct=292863"
    plain = "https://www.cardmarket.com/en/Magic/Products/Singles/Alliances/Force-of-Will?language=1"
    assert clean_cardmarket_url(plain) == plain
    assert clean_cardmarket_url(None) is None and clean_cardmarket_url("https://example.com/?utm_x=1") == "https://example.com/?utm_x=1"
    card = ScryfallCard.from_json({"id": "x", "name": "Force of Will", "set": "all", "set_name": "Alliances",
                                   "collector_number": "1", "prices": {}, "purchase_uris": {"cardmarket": scryfall_link}})
    assert card.cardmarket_url == "https://www.cardmarket.com/en/Magic/Products?idProduct=292863"

    # Links saved before are cleaned once.
    path = Path(tempfile.mkdtemp()) / "old.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE wishlistentry (id INTEGER PRIMARY KEY, cardmarket_url VARCHAR)")
        db.execute("INSERT INTO wishlistentry VALUES (1, ?)", (scryfall_link,))
        db.execute("INSERT INTO wishlistentry VALUES (2, ?)", (plain,))
        db.execute("PRAGMA user_version = 3")
    engine = create_engine(f"sqlite:///{path}")
    run_data_migrations(engine)
    engine.dispose()
    with sqlite3.connect(path) as db:
        urls = [row[0] for row in db.execute("SELECT cardmarket_url FROM wishlistentry ORDER BY id")]
        assert urls == ["https://www.cardmarket.com/en/Magic/Products?idProduct=292863", plain]
        assert db.execute("PRAGMA user_version").fetchone()[0] == 5


async def test_discord_price_alert_has_a_clean_cardmarket_link():
    import json

    import httpx

    from app.discord import DiscordNotifier
    from app.models import Card

    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(204)

    notifier = DiscordNotifier("https://discord.example/webhook", transport=httpx.MockTransport(handler))
    card = Card(name="Force of Will", set_code="all", set_name="Alliances", scryfall_id="x")
    link = "https://www.cardmarket.com/en/Magic/Products?idProduct=1&referrer=scryfall&utm_source=scryfall"
    assert await notifier.send_price_alert(card, 50.0, 60.0, link, "cardmarket")
    assert sent[0]["embeds"][0]["url"] == "https://www.cardmarket.com/en/Magic/Products?idProduct=1"
