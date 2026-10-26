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
