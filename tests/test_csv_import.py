import pytest

from app.csv_import import CsvFormatError, detect_format, parse_collection_csv
from app.services.collection import collection_rows, import_rows

ARCHIDEKT = """Quantity,Name,Finish,Condition,Date Added,Language,Purchase Price,Tags,Edition Name,Edition Code,Multiverse Id,Scryfall ID,Collector Number
2,Sol Ring,Normal,NM,2024-01-01,EN,,,Commander Masters,CMM,0,id-sol,1
1,"Lim-Dûl's Vault",Foil,NM,2024-01-01,EN,,,Alliances,ALL,0,,
1,Fire // Ice,Etched,NM,2024-01-01,EN,,,Modern Horizons 2,MH2,0,,
"""

MOXFIELD = """"Count","Tradelist Count","Name","Edition","Condition","Language","Foil","Tags","Last Modified","Collector Number","Alter","Proxy","Purchase Price"
"3","0","Sol Ring","cmm","Near Mint","English","","","2024-01-01 10:00:00.000000","1","False","False",""
"1","0","Fire // Ice","mh2","Near Mint","English","foil","","2024-01-01 10:00:00.000000","","False","False",""
"""


def test_detect_format():
    assert detect_format(ARCHIDEKT.splitlines()[0].split(",")) == "archidekt"
    assert detect_format(["Count", "Name", "Edition", "Foil"]) == "moxfield"
    assert detect_format(["qty", "Card Name"]) == "generic"
    with pytest.raises(CsvFormatError):
        detect_format(["foo", "bar"])


def test_parse_archidekt():
    parsed = parse_collection_csv(ARCHIDEKT)
    assert parsed.format == "archidekt"
    sol, vault, fire = parsed.rows
    assert (sol.name, sol.quantity, sol.set_code, sol.scryfall_id, sol.is_foil) == ("Sol Ring", 2, "cmm", "id-sol", False)
    assert (vault.name, vault.is_foil, vault.set_code) == ("Lim-Dûl's Vault", True, "all")
    assert (fire.name, fire.is_foil) == ("Fire // Ice", True)


def test_parse_moxfield():
    parsed = parse_collection_csv(MOXFIELD)
    assert parsed.format == "moxfield"
    sol, fire = parsed.rows
    assert (sol.quantity, sol.set_code, sol.collector_number, sol.is_foil) == (3, "cmm", "1", False)
    assert (fire.name, fire.is_foil) == ("Fire // Ice", True)


def test_parse_bytes_with_bom_and_special_characters():
    parsed = parse_collection_csv("﻿Quantity,Name\n4,Lim-Dûl's Vault\n".encode("utf-8"))
    assert parsed.format == "generic"
    assert parsed.rows[0].name == "Lim-Dûl's Vault" and parsed.rows[0].quantity == 4


def test_bad_rows_are_reported_not_fatal():
    parsed = parse_collection_csv("Quantity,Name\nlots,Sol Ring\n0,Fire // Ice\n,Black Lotus\n1,\n")
    assert [row.name for row in parsed.rows] == ["Black Lotus"]  # empty quantity defaults to 1
    assert len(parsed.errors) == 2 and "Sol Ring" in parsed.errors[0]


def test_empty_file():
    with pytest.raises(CsvFormatError):
        parse_collection_csv("")


async def test_import_rows_into_collection(session, scryfall):
    parsed = parse_collection_csv(ARCHIDEKT + "1,Not A Real Card,Normal,NM,,EN,,,,,,,\n")
    result = await import_rows(session, scryfall, parsed.rows)
    session.commit()

    assert (result.imported_rows, result.imported_cards) == (3, 4)
    assert result.not_found == ["Not A Real Card"]
    rows = {row.card.name: row for row in collection_rows(session)}
    assert rows["Sol Ring"].total_price == 3.0  # 2 x 1.50
    assert rows["Fire // Ice"].unit_price == 0.8  # foil price
    assert rows["Lim-Dûl's Vault"].unit_price is None  # foil, but no foil price known


async def test_import_twice_adds_up_unless_replace(session, scryfall):
    rows = parse_collection_csv(MOXFIELD).rows
    await import_rows(session, scryfall, rows)
    await import_rows(session, scryfall, rows)
    assert sum(row.entry.quantity for row in collection_rows(session)) == 8

    await import_rows(session, scryfall, rows, replace=True)
    assert sum(row.entry.quantity for row in collection_rows(session)) == 4


async def test_replace_keeps_the_cards_you_added_yourself(client):
    def collection():
        return {
            (i["card"]["name"], i["is_foil"]): i["quantity"] for i in client.get("/api/collection").json()["items"]
        }

    client.post("/api/collection", json={"name": "Lim-Dûl's Vault"})  # added by hand, not in the file
    client.post("/api/collection", json={"name": "Sol Ring", "set_code": "cmm", "quantity": 1})  # also in the file
    files = {"file": ("moxfield.csv", MOXFIELD, "text/csv")}
    result = client.post("/api/collection/import", params={"replace": True}, files=files).json()
    assert result["kept_manual"] == 1
    # The file's quantity is taken over for Sol Ring, so it is not counted twice.
    assert collection() == {("Lim-Dûl's Vault", False): 1, ("Sol Ring", False): 3, ("Fire // Ice", True): 1}

    # Importing again with replace changes nothing, and still keeps the hand-added card.
    client.post("/api/collection/import", params={"replace": True}, files=files)
    assert collection() == {("Lim-Dûl's Vault", False): 1, ("Sol Ring", False): 3, ("Fire // Ice", True): 1}

    # A card that is no longer in the file goes, a deck that used an imported card keeps its choice.
    deck = client.post("/api/decks", json={"name": "D", "decklist": "1 Sol Ring"}).json()
    sol = next(i for i in client.get("/api/collection").json()["items"] if i["card"]["name"] == "Sol Ring")
    client.put(f"/api/decks/{deck['id']}/cards/{deck['cards'][0]['entry_id']}/printing", json={"collection_entry_id": sol["id"]})
    only_sol = {"file": ("moxfield.csv", MOXFIELD.rsplit("\n", 2)[0] + "\n", "text/csv")}
    client.post("/api/collection/import", params={"replace": True}, files=only_sol)
    assert collection() == {("Lim-Dûl's Vault", False): 1, ("Sol Ring", False): 3}
    card = client.get(f"/api/decks/{deck['id']}").json()["cards"][0]
    assert card["chosen_collection_entry_id"] == sol["id"]


async def test_imported_cards_wait_until_you_put_them_in_a_deck(client):
    first = client.post("/api/decks", json={"name": "First", "decklist": "1 Sol Ring"}).json()
    second = client.post("/api/decks", json={"name": "Second", "decklist": "1 Sol Ring\n1 Fire // Ice"}).json()
    client.post("/api/collection", json={"name": "Lim-Dûl's Vault"})  # added by hand: not held back

    files = {"file": ("moxfield.csv", MOXFIELD, "text/csv")}  # 3 Sol Ring, 1 foil Fire // Ice
    result = client.post("/api/collection/import", files=files).json()
    assert result["new_copies"] == 4

    def deck(d):
        return {c["name"]: c for c in client.get(f"/api/decks/{d['id']}").json()["cards"]}

    # No deck got the new copies on its own.
    assert deck(first)["Sol Ring"]["missing"] == 1 and deck(second)["Sol Ring"]["missing"] == 1
    items = {i["card"]["name"]: i for i in client.get("/api/collection").json()["items"]}
    assert items["Sol Ring"]["waiting"] == 3 and items["Fire // Ice"]["waiting"] == 1
    assert items["Lim-Dûl's Vault"]["waiting"] == 0
    assert [d["name"] for d in items["Sol Ring"]["wanted_by"]] == ["First", "Second"]
    assert 'Show the cards to sort' in client.get("/collection").text

    # You put one in the second deck yourself; the first deck still waits.
    second_sol = deck(second)["Sol Ring"]["entry_id"]
    response = client.post(f"/api/decks/{second['id']}/cards/{second_sol}/new-copy")
    assert response.status_code == 200
    assert deck(second)["Sol Ring"]["missing"] == 0 and deck(first)["Sol Ring"]["missing"] == 1
    assert client.post(f"/api/decks/{second['id']}/cards/{second_sol}/new-copy").status_code == 400  # not missing it
    items = {i["card"]["name"]: i for i in client.get("/api/collection").json()["items"]}
    assert items["Sol Ring"]["waiting"] == 2

    # The page buttons do the same.
    page = client.post(
        f"/collection/{items['Fire // Ice']['id']}/sort",
        data={"deck_id": second["id"], "deck_entry_id": deck(second)["Fire // Ice"]["entry_id"]},
    )
    assert "Put 1× Fire // Ice in Second" in page.text
    assert deck(second)["Fire // Ice"]["missing"] == 0

    # "Let the app decide": the rest goes to the decks automatically again.
    assert client.post("/api/collection/let-app-sort").json() == {"released": 2}
    assert deck(first)["Sol Ring"]["missing"] == 0
    assert 'Show the cards to sort' not in client.get("/collection").text


async def test_only_the_latest_import_is_new(client):
    deck = client.post("/api/decks", json={"name": "Ring", "decklist": "1 Sol Ring"}).json()
    client.post("/api/collection/import", files={"file": ("moxfield.csv", MOXFIELD, "text/csv")})

    def items():
        return {i["card"]["name"]: i for i in client.get("/api/collection").json()["items"]}

    assert items()["Sol Ring"]["waiting"] == 3 and items()["Fire // Ice"]["waiting"] == 1

    # The next import: the copies of the first one are no longer new and go to the decks as usual.
    vault = MOXFIELD.split("\n")[0] + '\n"2","0","Lim-Dûl\'s Vault","all","Near Mint","English","","","2024-01-02 10:00:00.000000","","False","False",""\n'
    result = client.post("/api/collection/import", files={"file": ("moxfield.csv", vault, "text/csv")}).json()
    assert result["new_copies"] == 2
    now = items()
    assert now["Sol Ring"]["waiting"] == 0 and now["Fire // Ice"]["waiting"] == 0
    assert now["Lim-Dûl's Vault"]["waiting"] == 2
    card = client.get(f"/api/decks/{deck['id']}").json()["cards"][0]
    assert card["missing"] == 0  # the Sol Ring from the first import is in the deck now

    # More of a card that is already there: only the copies added now are new.
    client.post("/api/collection/import", files={"file": ("moxfield.csv", MOXFIELD, "text/csv")})
    now = items()
    assert now["Sol Ring"]["waiting"] == 3 and now["Lim-Dûl's Vault"]["waiting"] == 0
