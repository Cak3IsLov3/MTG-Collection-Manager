"""The names of the colour groups (Selesnya, Jund...) on the decks, and the groups you have no deck in yet."""

from app.services import colour_groups


def test_every_combination_of_colours_has_a_name():
    assert len(colour_groups.NAMES) == 32 and len(set(colour_groups.NAMES.values())) == 32
    assert colour_groups.group_name("WG") == "Selesnya" and colour_groups.group_name("GW") == "Selesnya"  # the order does not matter
    assert colour_groups.group_name("") == "Colourless" and colour_groups.group_name("R") == "Mono-red"
    assert colour_groups.group_name("BRG") == "Jund" and colour_groups.group_name("WUBRG") == "Five-colour"
    assert colour_groups.group_name("UBRG") == "Glint-Eye"
    assert colour_groups.group_name(None) is None  # the colours are not known yet


def test_the_overview_counts_decks_and_finds_the_missing_groups():
    groups = colour_groups.overview(["WB", "BW", "UBR", "", None])
    assert len(groups) == 32
    by_name = {g.name: g for g in groups}
    assert by_name["Orzhov"].decks == 2 and by_name["Grixis"].decks == 1 and by_name["Colourless"].decks == 1
    assert by_name["Selesnya"].missing and not by_name["Orzhov"].missing
    assert sum(g.decks for g in groups) == 4  # a deck whose colours are unknown is in no group
    assert groups[0].name == "Colourless" and groups[-1].name == "Five-colour"
    kinds = dict(colour_groups.by_kind(groups))
    assert [len(items) for items in kinds.values()] == [1, 5, 10, 10, 5, 1]


def test_the_deck_pages_show_the_group_and_what_is_missing(client):
    client.post("/api/collection", json={"name": "Lim-Dûl's Vault"})
    grixis = client.post("/api/decks", json={"name": "Grixis", "decklist": "1 Lim-Dûl's Vault\n1 Fire // Ice\n1 Sol Ring"}).json()
    client.post("/api/decks", json={"name": "Artifacts", "decklist": "1 Sol Ring\n1 Black Lotus"})
    page = client.get("/deck").text
    assert 'class="badge colour-group"' in page and ">Grixis</span>" in page and ">Colourless</span>" in page
    assert "you have decks in 2 of 32" in page and "30 still missing" in page
    assert 'aria-label="Colour groups you have no deck in yet"' in page
    missing = page.split('aria-label="Colour groups you have no deck in yet"')[1].split("</div>")[0]
    assert "Selesnya" in missing and "Jund" in missing and "Grixis" not in missing and "Colourless" not in missing
    detail = client.get(f"/decks/{grixis['id']}").text
    assert 'class="deck-colour-line"' in detail and ">Grixis</span>" in detail


def test_the_collection_that_fits_a_colour_group(client, session):
    client.post("/api/collection", json={"name": "Lim-Dûl's Vault", "quantity": 2})  # UBR
    client.post("/api/collection", json={"name": "Sol Ring"})  # colourless
    client.post("/api/collection", json={"name": "Fire // Ice"})  # UR
    pool = colour_groups.fitting_pool(session, "UBR")
    assert (pool.unique, pool.copies) == (3, 4)
    pool = colour_groups.fitting_pool(session, "UR")
    assert (pool.unique, pool.copies) == (2, 2)  # the Vault needs black as well
    assert colour_groups.fitting_pool(session, "").unique == 1  # only the colourless Sol Ring
    assert colour_groups.group_key("ub") == "UB" and colour_groups.group_key("c") == "" and colour_groups.group_key("nonsense") is None
    assert colour_groups.slug("UB") == "ub" and colour_groups.slug("") == "c"


def test_a_colour_group_page_lists_the_most_played_commanders_and_the_free_ones_you_own(client, monkeypatch):
    from app.scryfall import ScryfallClient
    from app.services import colour_groups

    asked = []
    colour_groups._COMMANDERS.clear()

    async def search(self, query, **params):
        asked.append((query, params))
        return [{"name": "Ezuri, Renegade Leader", "type_line": "Legendary Creature — Elf Warrior", "edhrec_rank": 99,
                 "scryfall_uri": "https://scryfall.com/card/e", "image_uris": {"normal": "https://cards.scryfall.io/normal/front/e.jpg"}},
                {"name": "Marwyn, the Nurturer", "type_line": "Legendary Creature — Elf Druid", "edhrec_rank": 1234,
                 "scryfall_uri": "https://scryfall.com/card/x", "image_uris": {"normal": "https://cards.scryfall.io/normal/front/m.jpg"}},
                {"name": "Marwyn, the Nurturer", "type_line": "again"},
                {"name": "Sol Ring", "type_line": "Artifact", "edhrec_rank": 5000},
                {"name": "Lim-Dûl's Vault", "type_line": "Instant", "edhrec_rank": 6000},
                {"name": "Ghalta", "card_faces": [{"image_uris": {"normal": "https://cards.scryfall.io/normal/front/g.jpg"}}]}]

    monkeypatch.setattr(ScryfallClient, "search", search)
    client.post("/api/collection", json={"name": "Lim-Dûl's Vault"})  # free: no deck uses it
    client.post("/api/collection", json={"name": "Sol Ring", "set_code": "cmm"})  # its only copy sits in a deck
    client.post("/api/collection", json={"name": "Marwyn, the Nurturer", "quantity": 2})  # one copy sits in a deck
    client.post("/api/decks", json={"name": "Marwyn, the Nurturer - Elves", "decklist": "1 Marwyn, the Nurturer"})
    client.post("/api/decks", json={"name": "Artifacts", "decklist": "1 Sol Ring"})
    client.get("/api/decks")
    page = client.get("/deck/colours/g")
    assert page.status_code == 200
    assert asked == [("is:commander legal:commander id=g", {"order": "edhrec", "unique": "cards"})]
    text = page.text
    owned, popular = text.split("Commanders you own that no deck uses")[1].split("Most played commanders")
    # free to use: the Vault, and Marwyn of which one of two copies is free; Sol Ring is in a deck, Ezuri and Ghalta are not owned
    assert "Marwyn, the Nurturer" in owned and "Lim-Dûl&#39;s Vault" in owned and "Sol Ring" not in owned
    assert "Ezuri" not in owned and "Ghalta" not in owned
    assert "You own <b>2</b> copies" in owned and "1 not in a deck" in owned and "in no deck yet" in owned
    assert owned.index("Marwyn") < owned.index("Lim-D")  # the most played first
    # the most played, in EDHREC's order and without duplicates, with a mark on those you own and can use
    assert popular.count('class="panel commander-card"') == 5 and popular.index("Ezuri") < popular.index("Marwyn")
    assert "#1234 on EDHREC" in popular and "https://cards.scryfall.io/normal/front/g.jpg" in popular
    assert popular.count("✓ in your collection") == 2
    assert client.get("/deck/colours/g").status_code == 200 and len(asked) == 1  # one search a day per group
    assert client.get("/deck/colours/c").status_code == 200 and asked[-1][0].endswith("id=c")
    assert client.get("/deck/colours/xyz").status_code == 404

    found = client.get("/api/commanders/coverage", params={"name": "Marwyn, the Nurturer"}).json()
    assert (found["decks"], found["of"]) == (1000, 3) and found["owned"] >= 1
    assert found["url"] == "https://edhrec.com/commanders/marwyn-the-nurturer"
    assert client.get("/api/commanders/coverage", params={"name": "Nobody"}).status_code == 502


def test_no_owned_commander_is_said_so(client, monkeypatch):
    from app.scryfall import ScryfallClient
    from app.services import colour_groups

    colour_groups._COMMANDERS.clear()

    async def search(self, query, **params):
        return [{"name": "Ezuri, Renegade Leader", "type_line": "Legendary Creature"}]

    monkeypatch.setattr(ScryfallClient, "search", search)
    text = client.get("/deck/colours/g").text
    assert "You own no free card that can be a commander in exactly these colours" in text and "Ezuri, Renegade Leader" in text
    assert "no deck in this group yet" in text


def test_the_deck_list_filters_and_sorts_on_the_colour_group(client):
    client.post("/api/collection", json={"name": "Lim-Dûl's Vault"})
    client.post("/api/decks", json={"name": "Grixis", "decklist": "1 Lim-Dûl's Vault\n1 Fire // Ice\n1 Sol Ring"})
    client.post("/api/decks", json={"name": "Artifacts", "decklist": "1 Sol Ring\n1 Black Lotus"})
    page = client.get("/deck").text
    assert 'id="deck-group-filter"' in page and '<option value="Grixis">Grixis (1)</option>' in page
    assert '<option value="Colourless">Colourless (1)</option>' in page and "Selesnya" not in page.split('id="deck-group-filter"')[1].split("</select>")[0]
    assert 'data-group="Grixis"' in page and 'data-group="Colourless"' in page
    assert 'data-sort="groupOrder"' in page
    order = {name: int(value) for name, value in __import__("re").findall(r'data-group="(\w+)" data-group-order="(\d+)"', page)}
    assert order["Colourless"] == 0 < order["Grixis"]  # colourless first
    assert 'href="/deck/colours/wg"' in page  # a group you do not play links to its commanders


def test_the_collection_spread_over_the_colours(client, session):
    from app.services import stats

    assert stats.colour_spread(session)["weakest"] is None  # nothing yet
    assert "Add cards to your collection" in client.get("/").text
    client.post("/api/collection", json={"name": "Lim-Dûl's Vault", "quantity": 2})  # UB, 12.34 each
    client.post("/api/collection", json={"name": "Sol Ring"})  # colourless
    client.post("/api/collection", json={"name": "Fire // Ice"})  # UR
    spread = stats.colour_spread(session)
    per = {c["letter"]: c for c in spread["colours"]}
    assert spread["copies"] == 4 and (per["U"]["copies"], per["B"]["copies"], per["R"]["copies"]) == (3, 2, 1)
    assert per["W"]["copies"] == per["G"]["copies"] == 0 and per["U"]["unique"] == 2
    assert per["B"]["value"] == 24.68 and per["U"]["bar"] == 100 and per["B"]["bar"] == 67 and per["B"]["share"] == 50
    assert (spread["multicolour"], spread["colourless"], spread["unknown"]) == (3, 1, 0)
    assert spread["strongest"]["letter"] == "U" and spread["weakest"]["name"] in ("White", "Green")
    page = client.get("/").text
    assert 'data-tab="colours"' in page and "Your collection in colours" in page and "weakest in" in page
    assert 'class="bar-B" style="width: 67%"' in page and "Multicolour: <b>3</b>" in page


async def test_the_summary_has_decks_new_cards_and_alerts_and_can_be_daily(client, session):
    from app.jobs import send_weekly_summary
    from app.services import stats
    from tests.test_more_extras import FakeNotifier

    client.post("/api/collection", json={"name": "Lim-Dûl's Vault", "quantity": 2})
    client.post("/api/collection", json={"name": "Sol Ring"})
    client.post("/api/decks", json={"name": "Vault", "decklist": "1 Lim-Dûl's Vault"})
    week = stats.weekly_summary(session)
    assert week["days"] == 7 and week["new_copies"] == 3
    assert [c["name"] for c in week["new_cards"]][0] == "Lim-Dûl's Vault" and week["top_card"]["name"] == "Lim-Dûl's Vault"
    assert week["price_drops"] == 0 and week["deck_gains"] == [] and week["deck_losses"] == []  # no price history yet
    lines = stats.weekly_lines(week)
    assert any(line.startswith("**New cards** 3 added this week · most valuable: Lim-Dûl's Vault ×2") for line in lines)
    assert any(line.startswith("**Most valuable card** Lim-Dûl's Vault €12.34") for line in lines)

    day = stats.weekly_summary(session, days=1)
    assert day["days"] == 1 and "added today" in " ".join(stats.weekly_lines(day))
    notifier = FakeNotifier()
    assert await send_weekly_summary(session, notifier, days=1) is True
    assert notifier.title == "Your day in Magic" and " – " not in notifier.sent[0][1]
    assert await send_weekly_summary(session, notifier) is True and notifier.title == "Your week in Magic"
    page = client.get("/").text
    assert "Most valuable new: Lim-Dûl&#39;s Vault ×2" in page and "Most valuable card:" in page


def test_basic_lands_follow_the_colours_of_the_commander_and_the_spells():
    from app.services.auto_deck import basic_lands

    assert basic_lands("", [], {}, 5) == {"Wastes": 5}
    assert basic_lands("G", ["a"], {}, 36) == {"Forest": 36}
    assert basic_lands("WU", [], {}, 0) == {}
    # more blue spells than white: more Islands, and both colours get lands, all 36 of them
    split = basic_lands("WU", ["a", "b", "c", "d"], {"a": "U", "b": "U", "c": "U", "d": "W"}, 36)
    assert sum(split.values()) == 36 and split["Island"] > split["Plains"] > 0


def test_a_deck_is_built_from_the_free_cards_you_own_with_lands(client, session, monkeypatch):
    from app.services import auto_deck
    from tests import conftest

    conftest.EDHREC_PAGES["marwyn-the-nurturer"]["container"]["json_dict"]["cardlists"].append(
        {"header": "Utility Lands", "tag": "utilitylands", "cardviews": [conftest.edhrec_view("Fire // Ice", 500)]})
    try:
        for name, extra in (("Marwyn, the Nurturer", {}), ("Sol Ring", {"set_code": "cmm"}), ("Lim-Dûl's Vault", {}),
                            ("Pitiless Plunderer", {}), ("Fire // Ice", {})):
            client.post("/api/collection", json={"name": name, **extra})
        client.post("/api/decks", json={"name": "Artifacts", "decklist": "1 Sol Ring"})  # the Sol Ring sits in this deck
        client.get("/api/decks")
        few = client.post("/deck/colours/build", data={"commander": "Marwyn, the Nurturer"}, follow_redirects=False)
        assert few.headers["location"].startswith("/deck?msg=Only%202%20free%20cards")  # not enough for a deck
        monkeypatch.setattr(auto_deck, "MIN_CARDS", 1)
        made = client.post("/deck/colours/build", data={"commander": "Marwyn, the Nurturer"}, follow_redirects=False)
        assert made.status_code == 303 and made.headers["location"].startswith("/decks/")
        deck = next(d for d in client.get("/api/decks").json() if d["name"] == "Marwyn, the Nurturer - from my collection")
        detail = client.get(f"/api/decks/{deck['id']}").json()
        names = {card["name"] for card in detail["cards"]}
        # the commander, the cards most played with it that are free and the free land of its list; no Sol Ring (in another deck)
        assert names == {"Marwyn, the Nurturer", "Lim-Dûl's Vault", "Pitiless Plunderer", "Fire // Ice"} and detail["commander"] == "Marwyn, the Nurturer"
        page = client.get(made.headers["location"]).text
        assert "Built a deck of 2 cards you own, with 1 land you own and 36 basic lands" in page and "1 more you own are in other decks" in page
        assert "36 basic lands" in page

        # a commander EDHREC does not know
        assert "Could not build a deck for Nobody" in client.post("/deck/colours/build", data={"commander": "Nobody"}).text
        # the deck just made holds the only copy of the commander now
        again = client.post("/deck/colours/build", data={"commander": "Marwyn, the Nurturer"}).text
        assert "no free copy" in again  # its only copy is in the deck just made
    finally:
        conftest.EDHREC_PAGES["marwyn-the-nurturer"]["container"]["json_dict"]["cardlists"].pop()


def test_the_build_button_is_on_the_commanders_you_own(client, monkeypatch):
    from app.scryfall import ScryfallClient
    from app.services import colour_groups

    colour_groups._COMMANDERS.clear()

    async def search(self, query, **params):
        return [{"name": "Marwyn, the Nurturer", "type_line": "Legendary Creature"}, {"name": "Ezuri", "type_line": "Legendary Creature"}]

    monkeypatch.setattr(ScryfallClient, "search", search)
    client.post("/api/collection", json={"name": "Marwyn, the Nurturer"})
    text = client.get("/deck/colours/g").text
    owned, popular = text.split("Commanders you own that no deck uses")[1].split("Most played commanders")
    assert owned.count("Build a deck from my cards") == 1 and 'value="Marwyn, the Nurturer"' in owned
    assert "Build a deck" not in popular


def test_the_colour_group_styles_do_not_touch_the_rows_of_the_collection_table():
    """The collection table calls the line of a card with several versions "group-row": no style of its own may
    make it a flex box, or the cells of that line pile up under each other."""
    import re
    from pathlib import Path

    css = (Path(__file__).resolve().parent.parent / "app" / "static" / "style.css").read_text(encoding="utf-8")
    for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        if re.search(r"(?<![\w-])\.group-row\b", selector) and "tr.group-row" not in selector:
            raise AssertionError(f"{selector.strip()} would style the group line of the collection table: {body.strip()}")
