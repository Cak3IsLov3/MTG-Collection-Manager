"""Combos, suggestions per deck, the daily backup and the Discord message about Archidekt changes."""

import datetime as dt
import sqlite3

from app.backup import database_path, list_backups, make_backup
from app.jobs import deck_change_message, notify_deck_changes
from app.models import Deck
from app.services.decks import SyncResult

DECK = "1 Rhystic Study\n1 Time Warp\n1 Sol Ring\n1 Lim-Dûl's Vault"


# --- combos -------------------------------------------------------------------------------------


def test_combos_of_a_deck(client, fake_spellbook):
    client.post("/api/collection", json={"name": "Marwyn, the Nurturer"})
    deck = client.post("/api/decks", json={"name": "Combo", "decklist": DECK, "bracket": 2}).json()
    combos = client.get(f"/api/decks/{deck['id']}/combos").json()
    assert [c["cards"] for c in combos["included"]] == [["Rhystic Study", "Time Warp"]]
    assert combos["included"][0]["two_card"] is True
    assert combos["included"][0]["url"] == "https://commanderspellbook.com/combo/1-2/"
    assert sorted(c["missing"][0] for c in combos["one_card_away"]) == ["Armageddon", "Marwyn, the Nurturer"]

    # The two-card combo counts for the bracket.
    check = client.get(f"/api/decks/{deck['id']}/bracket-check").json()
    assert check["two_card_combos"] == ["Rhystic Study + Time Warp"]
    assert any("two-card combo" in problem for problem in check["problems"])

    page = client.get(f"/decks/{deck['id']}").text
    assert "One card away" in page and "Two-card combos: 1" in page
    assert 'Marwyn, the Nurturer</strong> <span class="badge hit" title="You have this card">you have it' in page

    # Asked once; again only when the cards of the deck change.
    asked = len(fake_spellbook.requests)
    client.get(f"/decks/{deck['id']}")
    assert len(fake_spellbook.requests) == asked
    client.post(f"/api/decks/{deck['id']}/cards", json={"name": "Armageddon"})
    combos = client.get(f"/api/decks/{deck['id']}/combos").json()
    assert len(fake_spellbook.requests) == asked + 1
    assert ["Sol Ring", "Lim-Dûl's Vault", "Armageddon"] in [c["cards"] for c in combos["included"]]


def test_deck_page_without_commander_spellbook(client, fake_spellbook):
    fake_spellbook.down = True
    deck = client.post("/api/decks", json={"name": "Combo", "decklist": DECK, "bracket": 2}).json()
    page = client.get(f"/decks/{deck['id']}")
    assert page.status_code == 200 and "Two-card combos: ?" in page.text and "could not be reached" in page.text
    assert client.get(f"/api/decks/{deck['id']}/combos").status_code == 502


# --- suggestions --------------------------------------------------------------------------------


def test_suggestions_from_the_collection(client):
    for name in ("Sol Ring", "Lim-Dûl's Vault", "Marwyn, the Nurturer", "Fire // Ice"):
        client.post("/api/collection", json={"name": name})
    client.post("/api/collection", json={"name": "Rhystic Study"})
    deck = client.post("/api/decks", json={"name": "Blue black", "decklist": "1 Rhystic Study\n1 Lim-Dûl's Vault"}).json()
    client.get(f"/decks/{deck['id']}")  # works out the colours of the deck

    found = client.get(f"/api/decks/{deck['id']}/suggestions").json()
    names = {card["name"] for group in found["groups"] for card in group["cards"]}
    # Sol Ring is colourless, so it fits; Marwyn (green) and Fire // Ice (red) do not, and the deck's
    # own cards are not suggested.
    assert names == {"Sol Ring"}
    ramp = next(group for group in found["groups"] if group["group"] == "ramp")
    assert (ramp["deck_has"], ramp["aim_for"], ramp["cards"][0]["free"]) == (0, 10, 1)

    page = client.get(f"/decks/{deck['id']}").text
    assert "Suggestions from your collection" in page and "Add to deck" in page and "10 short" in page

    # A card another deck uses is not free.
    client.post("/api/decks", json={"name": "Artifacts", "decklist": "1 Sol Ring"})
    found = client.get(f"/api/decks/{deck['id']}/suggestions").json()
    assert found["groups"] == []


def test_suggestions_for_a_deck_that_follows_archidekt(client, session):
    client.post("/api/collection", json={"name": "Sol Ring"})
    deck = client.post("/api/decks", json={"name": "Linked", "decklist": "1 Rhystic Study"}).json()
    linked = session.get(Deck, deck["id"])
    linked.archidekt_id = 123
    session.add(linked)
    session.commit()
    page = client.get(f"/decks/{deck['id']}").text
    assert "This deck follows Archidekt: add a card there" in page and "Add to deck" not in page


# --- backup -------------------------------------------------------------------------------------


def test_backup(tmp_path):
    source = tmp_path / "mtg.db"
    with sqlite3.connect(source) as db:
        db.execute("CREATE TABLE card (name TEXT)")
        db.execute("INSERT INTO card VALUES ('Sol Ring')")
    url = f"sqlite:///{source}"
    folder = tmp_path / "backups"
    for day in (1, 2, 3):
        made = make_backup(url, folder, keep=2, today=dt.date(2026, 10, day))
    assert made == folder / "mtg-2026-10-03.db"
    assert sorted(path.name for path in folder.glob("*.db")) == ["mtg-2026-10-02.db", "mtg-2026-10-03.db"]
    copy = sqlite3.connect(made)
    assert copy.execute("SELECT name FROM card").fetchall() == [("Sol Ring",)]
    copy.close()
    assert [backup.name for backup in list_backups(folder, "mtg")] == ["mtg-2026-10-03.db", "mtg-2026-10-02.db"]

    # Nothing to copy: a database in memory, or backups switched off.
    assert make_backup("sqlite://", folder, keep=2) is None
    assert make_backup(url, folder, keep=0) is None
    assert database_path("sqlite:///./mtg.db").name == "mtg.db" and database_path("postgresql://x") is None


def test_dashboard_mentions_backups(client):
    page = client.get("/").text
    assert "Backups" in page  # the tests run on a database in memory
    assert "not one" in page


# --- Archidekt changes on Discord ---------------------------------------------------------------


class FakeNotifier:
    configured = True

    def __init__(self):
        self.sent = []

    async def send_deck_changes(self, changes):
        self.sent.append(changes)
        return True


async def test_deck_change_message(client, session):
    client.post("/api/collection", json={"name": "Sol Ring", "set_code": "cmm"})
    saved = client.post("/api/decks", json={"name": "Ramp", "decklist": "1 Sol Ring\n2 Lim-Dûl's Vault"}).json()
    deck = session.get(Deck, saved["id"])
    deck.archidekt_id = 42
    result = SyncResult(deck, added=["Sol Ring", "Lim-Dûl's Vault"], removed=["Fire // Ice"])

    message = deck_change_message(session, result)
    assert message["name"] == "Ramp" and message["url"] == "https://archidekt.com/decks/42"
    assert message["lines"] == [
        "**Added** Sol Ring, Lim-Dûl's Vault",
        "**Removed** Fire // Ice",
        "**You still need** 2× Lim-Dûl's Vault",
    ]
    assert deck_change_message(session, SyncResult(deck)) is None  # nothing changed
    assert deck_change_message(session, SyncResult(deck, error="HTTP 500")) is None

    notifier = FakeNotifier()
    assert await notify_deck_changes(session, [result, SyncResult(deck)], notifier) == 1
    assert notifier.sent[0][0]["lines"][-1] == "**You still need** 2× Lim-Dûl's Vault"


# --- token suggestions ----------------------------------------------------------------------


def test_tokens_needed_from_rules_text():
    from app.services.deck_insights import tokens_needed

    assert tokens_needed("Create two 1/1 white Soldier creature tokens.") == 2
    assert tokens_needed("Whenever another creature you control dies, create a Treasure token.") == 3
    assert tokens_needed("Create X 1/1 green Saproling creature tokens.") == 3
    assert tokens_needed("At the beginning of your upkeep, create four 1/1 tokens.") == 10  # at most 10 per card
    assert tokens_needed("Destroy target creature.") == 1


def test_token_suggestions(client, session):
    from sqlmodel import select

    from app.models import DeckToken

    deck = client.post("/api/decks", json={"name": "Tokens", "decklist": "1 Pitiless Plunderer\n2 Raise the Alarm\n1 Sol Ring"}).json()
    client.get(f"/decks/{deck['id']}")  # looks the cards up on Scryfall
    found = {s["name"]: s for s in client.get(f"/api/decks/{deck['id']}/token-suggestions").json()}
    assert found["Treasure"]["needed"] == 3 and found["Treasure"]["made_by"] == ["Pitiless Plunderer"]
    assert found["Soldier"]["needed"] == 4  # two copies that make two each
    assert {s["status"] for s in found.values()} == {"missing"}

    # One by hand, too few: "short"; then take them all over.
    client.post(f"/decks/{deck['id']}/tokens", data={"name": "Treasure", "quantity": 1})
    found = {s["name"]: s for s in client.get(f"/api/decks/{deck['id']}/token-suggestions").json()}
    assert (found["Treasure"]["status"], found["Treasure"]["in_list"]) == ("short", 1)
    page = client.get(f"/decks/{deck['id']}").text
    assert "Token suggestions" in page and "Take over all 2" in page and "Set to 3" in page and "Add 4" in page

    client.post(f"/decks/{deck['id']}/tokens/suggested", data={"scryfall_id": "id-soldier"})
    session.expire_all()
    tokens = {t.name: t.quantity for t in session.exec(select(DeckToken))}
    assert tokens == {"Soldier": 4, "Treasure": 1}
    client.post(f"/decks/{deck['id']}/tokens/suggested", data={})
    found = client.get(f"/api/decks/{deck['id']}/token-suggestions").json()
    assert {s["status"] for s in found} == {"ok"} and {s["in_list"] for s in found} == {3, 4}


def test_standard_tokens_are_left_out(client, monkeypatch):
    import dataclasses

    from app import config

    deck = client.post("/api/decks", json={"name": "Tokens", "decklist": "1 Pitiless Plunderer\n1 Raise the Alarm"}).json()
    client.get(f"/decks/{deck['id']}")
    settings = dataclasses.replace(config.get_settings(), standard_tokens=("treasure",))
    monkeypatch.setattr(config, "get_settings", lambda: settings)
    names = [s["name"] for s in client.get(f"/api/decks/{deck['id']}/token-suggestions").json()]
    assert names == ["Soldier"]


# --- EDHREC -------------------------------------------------------------------------------------


def test_commander_slug():
    from app.edhrec import commander_slug

    assert commander_slug("Marwyn, the Nurturer") == "marwyn-the-nurturer"
    assert commander_slug("Optimus Prime, Hero // Optimus Prime, Autobot Leader") == "optimus-prime-hero"
    assert commander_slug("Lim-Dûl the Necromancer") == "lim-dul-the-necromancer"
    assert commander_slug("Kenrith's Kingdom") == "kenriths-kingdom"


def test_edhrec_comparison(client, fake_edhrec):
    client.post("/api/collection", json={"name": "Lim-Dûl's Vault"})
    client.post("/api/collection", json={"name": "Sol Ring", "set_code": "cmm"})
    deck = client.post("/api/decks", json={"name": "Marwyn, the Nurturer - Elves", "decklist": "1 Marwyn, the Nurturer\n1 Pitiless Plunderer"}).json()
    client.post("/api/decks", json={"name": "Artifacts", "decklist": "1 Sol Ring"})  # uses the Sol Ring
    client.get("/api/decks")  # works out the commander from the deck name
    found = client.get(f"/api/decks/{deck['id']}/edhrec").json()
    assert found["commander"] == "Marwyn, the Nurturer" and found["decks"] == 1000 and found["in_deck"] == 1
    cards = {c["name"]: c for c in found["cards"]}
    # The commander and basic lands are left out; Sol Ring once, under its type list.
    assert list(cards) == ["Sol Ring", "Lim-Dûl's Vault", "Pitiless Plunderer"]
    assert (cards["Sol Ring"]["inclusion"], cards["Sol Ring"]["category"]) == (95, "Mana Artifacts")
    assert (cards["Sol Ring"]["owned"], cards["Sol Ring"]["free"]) == (1, 0)
    assert (cards["Lim-Dûl's Vault"]["free"], cards["Lim-Dûl's Vault"]["synergy"]) == (1, 25)
    assert cards["Pitiless Plunderer"]["in_deck"] is True

    page = client.get(f"/decks/{deck['id']}").text
    assert "EDHREC" in page and "1 free" in page and "in other decks" in page and "https://edhrec.com/commanders/marwyn-the-nurturer" in page
    asked = len(fake_edhrec.requests)
    client.get(f"/decks/{deck['id']}")
    assert len(fake_edhrec.requests) == asked  # once a day per commander

    # A commander EDHREC does not know.
    other = client.post("/api/decks", json={"name": "Vault", "decklist": "1 Lim-Dûl's Vault"}).json()
    client.put(f"/api/decks/{other['id']}/commander", json={"commander": "Lim-Dûl's Vault"})
    assert client.get(f"/api/decks/{other['id']}/edhrec").status_code == 502
    assert "EDHREC has no page for Lim-Dûl" in client.get(f"/decks/{other['id']}").text


# --- reprints -----------------------------------------------------------------------------------


class ReprintNotifier:
    configured = True

    def __init__(self):
        self.sent = []

    async def send_reprints(self, lines):
        self.sent.append(lines)
        return True


async def test_reprint_alerts(client, session, scryfall, fake_scryfall):
    from tests.conftest import scryfall_json

    from app.jobs import check_reprints

    client.post("/api/collection", json={"name": "Lim-Dûl's Vault", "quantity": 2})  # €12.34
    client.post("/api/collection", json={"name": "Sol Ring", "set_code": "cmm"})  # €1.50: too cheap
    soon = (dt.date.today() + dt.timedelta(days=20)).isoformat()
    fake_scryfall.cards += [
        scryfall_json("Lim-Dûl's Vault", "id-vault-new", "fdn", eur="9.00", reprint=True, released_at=soon),
        scryfall_json("Lim-Dûl's Vault", "id-vault-borderless", "fdn", eur="15.00", reprint=True, released_at=soon),
        scryfall_json("Sol Ring", "id-sol-new", "fdn", eur="1.00", reprint=True, released_at=soon),
    ]
    notifier = ReprintNotifier()
    alerts = await check_reprints(session, scryfall, notifier, min_eur=5, days=60)
    assert [(a.new_scryfall_id, a.price, a.copies, a.notified) for a in alerts] == [("id-vault-new", 12.34, 2, True)]
    assert notifier.sent[0][0].startswith("**Lim-Dûl's Vault** · reprinted in Set FDN (FDN, out ")
    assert "you have 2" in notifier.sent[0][0]

    # Told once; off with 0.
    assert await check_reprints(session, scryfall, notifier, min_eur=5, days=60) == []
    assert await check_reprints(session, scryfall, notifier, min_eur=0, days=60) == []
    page = client.get("/").text
    assert "Reprints of cards you own" in page and "Set FDN (FDN)" in page and f"out {soon}" in page
    assert client.get("/api/reprints").json()[0]["card"] == "Lim-Dûl's Vault"
