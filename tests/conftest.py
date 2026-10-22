import os

# Must be set before the app is imported: in-memory database, no background jobs.
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["SCHEDULER_ENABLED"] = "false"
os.environ["DISCORD_WEBHOOK_URL"] = ""
# The stream overlay keeps its files in a folder of its own, not in the project.
import tempfile  # noqa: E402

os.environ["STREAM_STATE"] = os.path.join(tempfile.mkdtemp(prefix="mtg-stream-"), "stream-state.json")

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel

from app.database import engine, get_session
from app.deps import get_edhrec, get_scryfall, get_spellbook
from app.edhrec import EdhrecClient
from app.main import app
from app.scryfall import ScryfallClient
from app.spellbook import SpellbookClient


def scryfall_json(name, scryfall_id, set_code="tst", eur="1.00", eur_foil=None, **extra) -> dict:
    return {
        "id": scryfall_id,
        "name": name,
        "set": set_code,
        "set_name": f"Set {set_code.upper()}",
        "collector_number": "1",
        "image_uris": {"normal": f"https://img.example/{scryfall_id}.jpg"},
        "prices": {"eur": eur, "eur_foil": eur_foil},
        "purchase_uris": {"cardmarket": f"https://www.cardmarket.com/en/Magic/Products?idProduct={scryfall_id}"},
        **extra,
    }


CARDS = [
    scryfall_json("Sol Ring", "id-sol", "cmm", eur="1.50", eur_foil="4.00", color_identity=[], rarity="uncommon",
                  type_line="Artifact", cmc=1.0, oracle_text="{T}: Add {C}{C}."),
    scryfall_json("Sol Ring", "id-sol-lea", "lea", eur="900.00"),
    scryfall_json("Sol Ring", "id-sol-cmm-art", "cmm", eur="6.00", collector_number="410"),
    scryfall_json("Lim-Dûl's Vault", "id-vault", "all", eur="12.34", color_identity=["B", "U"],
                  type_line="Instant", cmc=2.0, oracle_text="Look at the top five cards of your library."),
    scryfall_json("Fire // Ice", "id-fire", "mh2", eur="0.25", eur_foil="0.80", color_identity=["R", "U"],
                  type_line="Instant // Instant", cmc=4.0,
                  card_faces=[{"oracle_text": "Fire deals 2 damage to any target."}, {"oracle_text": "Tap target permanent. Draw a card."}]),
    scryfall_json("Black Lotus", "id-lotus", "lea", eur=None),
    scryfall_json("Marwyn, the Nurturer", "id-marwyn", "dom", eur="2.00", color_identity=["G"],
                  type_line="Legendary Creature — Elf Druid", cmc=3.0,
                  oracle_text="Whenever an Elf enters, put a +1/+1 counter on Marwyn. {T}: Add {G}."),
    scryfall_json("Treasure", "id-treasure", "tc21", eur=None, type_line="Token Artifact — Treasure", layout="token"),
    scryfall_json("Elspeth, Sun's Champion Emblem", "id-elspeth-emblem", "tths", eur=None,
                  type_line="Emblem — Elspeth", layout="emblem"),
    scryfall_json("Elspeth, Sun's Champion", "id-elspeth", "ths", eur="4.00", color_identity=["W"],
                  type_line="Legendary Planeswalker — Elspeth", cmc=6.0,
                  oracle_text="+1: Create three 1/1 white Soldier creature tokens.\n−3: Destroy all creatures with power 4 or greater.\n"
                              "−7: You get an emblem with \"Creatures you control get +2/+2 and have flying.\"",
                  all_parts=[{"component": "combo_piece", "id": "id-elspeth", "name": "Elspeth, Sun's Champion", "type_line": "Legendary Planeswalker — Elspeth"},
                             {"component": "combo_piece", "id": "id-elspeth-emblem", "name": "Elspeth, Sun's Champion Emblem", "type_line": "Emblem — Elspeth"},
                             {"component": "token", "id": "id-soldier", "name": "Soldier", "type_line": "Token Creature — Soldier"}]),
    scryfall_json("Wolf // Rabbit", "id-wolf-rabbit", "tc22", eur=None, type_line="Token Creature — Wolf // Token Creature — Rabbit",
                  layout="double_faced_token", image_uris=None,
                  card_faces=[{"name": "Wolf", "image_uris": {"normal": "https://img.example/wolf-front.jpg"}},
                              {"name": "Rabbit", "image_uris": {"normal": "https://img.example/wolf-back.jpg"}}]),
    scryfall_json("Monster // Sorcerer", "id-monster-sorcerer", "twoe", eur=None, type_line="Token Enchantment — Aura Role",
                  layout="flip", card_faces=[{"name": "Monster"}, {"name": "Sorcerer"}]),
    scryfall_json("Cat Beast", "id-cat-beast", "tc13", eur=None, type_line="Token Creature — Cat Beast", layout="token"),
    scryfall_json("Morph", "id-morph", "tktk", eur=None, type_line="Creature", layout="token", power="2", toughness="2"),
    scryfall_json("Angel", "id-angel-44", "tdmu", eur=None, type_line="Token Creature — Angel", layout="token",
                  power="4", toughness="4", colors=["W"], oracle_text="Flying"),
    scryfall_json("Rhystic Study", "id-rhystic", "jmp", eur="30.00", color_identity=["U"], type_line="Enchantment",
                  cmc=3.0, game_changer=True,
                  oracle_text="Whenever an opponent casts a spell, you may draw a card unless that player pays {1}."),
    scryfall_json("Armageddon", "id-armageddon", "ema", eur="5.00", color_identity=["W"], type_line="Sorcery",
                  cmc=4.0, oracle_text="Destroy all lands."),
    scryfall_json("Time Warp", "id-time-warp", "e01", eur="4.00", color_identity=["U"], type_line="Sorcery",
                  cmc=5.0, oracle_text="Target player takes an extra turn after this one."),
    scryfall_json("Pitiless Plunderer", "id-plunderer", "rix", eur="0.50", color_identity=["B"],
                  type_line="Creature — Human Pirate", cmc=4.0,
                  oracle_text="Whenever another creature you control dies, create a Treasure token.",
                  all_parts=[{"component": "token", "id": "id-treasure", "name": "Treasure"},
                             {"component": "combo_piece", "id": "id-plunderer", "name": "Pitiless Plunderer"}]),
    scryfall_json("Raise the Alarm", "id-alarm", "m21", eur="0.10", color_identity=["W"], type_line="Instant", cmc=2.0,
                  oracle_text="Create two 1/1 white Soldier creature tokens.",
                  all_parts=[{"component": "token", "id": "id-soldier", "name": "Soldier"}]),
    scryfall_json("Soldier", "id-soldier", "tm21", eur=None, type_line="Token Creature — Soldier", layout="token"),
    scryfall_json("Angel", "id-angel-11", "tsnc", eur=None, type_line="Token Creature — Angel", layout="token",
                  power="1", toughness="1", colors=["W"], oracle_text="Flying, lifelink"),
]


class FakeScryfall:
    """Minimal in-memory Scryfall, served through httpx.MockTransport."""

    def __init__(self, cards=None):
        self.cards = [dict(card) for card in (cards or CARDS)]
        self.requests: list[httpx.Request] = []

    def set_price(self, scryfall_id, eur, eur_foil=None):
        card = next(c for c in self.cards if c["id"] == scryfall_id)
        card["prices"] = {"eur": eur, "eur_foil": eur_foil}

    def _find(self, identifier: dict):
        for card in self.cards:
            names = {card["name"].lower(), *card["name"].lower().split(" // ")}
            if "id" in identifier:
                if card["id"] == identifier["id"]:
                    return card
            elif "collector_number" in identifier:
                if (card["set"], card["collector_number"]) == (identifier["set"], identifier["collector_number"]):
                    return card
            elif identifier["name"].lower() in names and identifier.get("set") in (None, card["set"]):
                return card
        return None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        import json

        self.requests.append(request)
        not_found = httpx.Response(404, json={"object": "error", "status": 404})
        if request.url.path == "/cards/named":
            params = request.url.params
            name = params.get("exact")
            if name is None:  # fuzzy: match on prefix
                fuzzy = params["fuzzy"].lower()
                match = next((c for c in self.cards if c["name"].lower().startswith(fuzzy)), None)
                return httpx.Response(200, json=match) if match else not_found
            card = self._find({"name": name, **({"set": params["set"]} if "set" in params else {})})
            return httpx.Response(200, json=card) if card else not_found
        if request.url.path == "/cards/search":
            import re

            if "is:commander" in request.url.params["q"]:
                wanted = re.search(r'name:"([^"]+)"', request.url.params["q"]).group(1).lower()
                found = [card for card in self.cards if "Legendary Creature" in card.get("type_line", "")
                         and wanted in card["name"].lower()]
                return httpx.Response(200, json={"data": found, "has_more": False}) if found else not_found
            if "is:reprint" in request.url.params["q"]:
                reprints = [card for card in self.cards if card.get("reprint")]
                return httpx.Response(200, json={"data": reprints, "has_more": False}) if reprints else not_found
            if "is:gamechanger" in request.url.params["q"]:
                changers = [card for card in self.cards if card.get("game_changer")]
                return httpx.Response(200, json={"data": changers, "has_more": False}) if changers else not_found
            wanted = {name.lower() for name in re.findall(r'!"([^"]+)"', request.url.params["q"])}
            prints = [card for card in self.cards if card["name"].lower() in wanted]
            if not prints:
                return not_found
            return httpx.Response(200, json={"data": prints, "has_more": False})
        if request.url.path == "/sets":
            codes = sorted({card["set"] for card in self.cards})
            return httpx.Response(200, json={"data": [
                {"code": code, "icon_svg_uri": f"https://svgs.scryfall.io/sets/{code}.svg"} for code in codes]})
        if request.url.host == "svgs.scryfall.io":
            return httpx.Response(200, content=b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><circle cx="16" cy="16" r="14"/></svg>',
                                  headers={"Content-Type": "image/svg+xml"})
        if request.url.path == "/cards/autocomplete":
            query = request.url.params["q"].lower()
            extras = request.url.params.get("include_extras") == "true"
            names = sorted({card["name"] for card in self.cards if query in card["name"].lower()
                            and (extras or card.get("layout") not in ("token", "emblem"))})
            return httpx.Response(200, json={"object": "catalog", "data": names})
        if request.url.path == "/cards/collection":
            data, missing = [], []
            for identifier in json.loads(request.content)["identifiers"]:
                card = self._find(identifier)
                (data if card else missing).append(card or identifier)
            return httpx.Response(200, json={"data": data, "not_found": missing})
        parts = request.url.path.strip("/").split("/")
        if len(parts) == 3:  # /cards/{set}/{collector_number}
            card = self._find({"set": parts[1], "collector_number": parts[2]})
            return httpx.Response(200, json=card) if card else not_found
        card = self._find({"id": parts[-1]})
        return httpx.Response(200, json=card) if card else not_found


def spellbook_combo(combo_id, cards, produces, popularity=100) -> dict:
    return {
        "id": combo_id, "popularity": popularity, "manaValueNeeded": 4,
        "uses": [{"card": {"name": name}} for name in cards],
        "produces": [{"feature": {"name": name}} for name in produces],
    }


# Combos the fake Commander Spellbook knows: (id, cards, effects).
COMBOS = [
    ("1-2", ["Rhystic Study", "Time Warp"], ["Infinite turns"]),  # made up, for the tests
    ("3-4", ["Sol Ring", "Lim-Dûl's Vault", "Armageddon"], ["Infinite colorless mana"]),
    ("5-6", ["Sol Ring", "Marwyn, the Nurturer"], ["Infinite green mana"]),
]


class FakeSpellbook:
    """Commander Spellbook's find-my-combos, served through httpx.MockTransport."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.down = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        import json

        self.requests.append(request)
        if self.down:
            return httpx.Response(503)
        body = json.loads(request.content)
        deck = {item["card"] for item in body["main"] + body["commanders"]}
        included, almost = [], []
        for combo_id, cards, produces in COMBOS:
            missing = [name for name in cards if name not in deck]
            if not missing:
                included.append(spellbook_combo(combo_id, cards, produces))
            elif len(missing) == 1:
                almost.append(spellbook_combo(combo_id, cards, produces))
        return httpx.Response(200, json={"count": None, "next": None, "results": {
            "identity": "", "included": included, "almostIncluded": almost,
        }})


def edhrec_view(name, decks, synergy=0.1) -> dict:
    return {"name": name, "num_decks": decks, "potential_decks": 1000, "synergy": synergy}


# EDHREC's page for Marwyn, the only commander the fake knows.
EDHREC_PAGES = {
    "marwyn-the-nurturer": {"container": {"json_dict": {"cardlists": [
        {"header": "Top Cards", "tag": "topcards", "cardviews": [edhrec_view("Sol Ring", 950, 0.0)]},
        {"header": "Creatures", "tag": "creatures", "cardviews": [
            edhrec_view("Marwyn, the Nurturer", 1000), edhrec_view("Pitiless Plunderer", 120, 0.08),
        ]},
        {"header": "Instants", "tag": "instants", "cardviews": [edhrec_view("Lim-Dûl's Vault", 300, 0.25)]},
        {"header": "Mana Artifacts", "tag": "manaartifacts", "cardviews": [edhrec_view("Sol Ring", 950, 0.0)]},
        {"header": "Lands", "tag": "lands", "cardviews": [edhrec_view("Forest", 999)]},
    ]}}},
}


class FakeEdhrec:
    def __init__(self):
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == "/pages/top/salt.json":
            return httpx.Response(200, json={"container": {"json_dict": {"cardlists": [{"cardviews": [
                {"name": "Sol Ring", "salt": 1.5}, {"name": "Rhystic Study", "salt": 1.2},
            ], "more": None}]}}})
        slug = request.url.path.removeprefix("/pages/commanders/").removesuffix(".json")
        page = EDHREC_PAGES.get(slug)
        return httpx.Response(200, json=page) if page else httpx.Response(404)


@pytest.fixture(autouse=True)
def clean_db():
    SQLModel.metadata.drop_all(engine)
    SQLModel.metadata.create_all(engine)
    yield


@pytest.fixture
def session():
    with Session(engine) as session:
        yield session


@pytest.fixture
def fake_scryfall():
    return FakeScryfall()


@pytest.fixture
def scryfall(fake_scryfall):
    return ScryfallClient(transport=httpx.MockTransport(fake_scryfall), min_interval=0)


@pytest.fixture
def fake_spellbook():
    return FakeSpellbook()


@pytest.fixture
def spellbook(fake_spellbook):
    return SpellbookClient(transport=httpx.MockTransport(fake_spellbook), min_interval=0)


@pytest.fixture
def fake_edhrec():
    return FakeEdhrec()


@pytest.fixture
def edhrec(fake_edhrec):
    return EdhrecClient(transport=httpx.MockTransport(fake_edhrec), min_interval=0)


@pytest.fixture
def client(scryfall, spellbook, edhrec):
    app.dependency_overrides[get_scryfall] = lambda: scryfall
    app.dependency_overrides[get_spellbook] = lambda: spellbook
    app.dependency_overrides[get_edhrec] = lambda: edhrec
    yield TestClient(app)
    app.dependency_overrides.clear()
