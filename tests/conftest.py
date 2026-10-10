import os

# Must be set before the app is imported: in-memory database, no background jobs.
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["SCHEDULER_ENABLED"] = "false"
os.environ["DISCORD_WEBHOOK_URL"] = ""

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel

from app.database import engine, get_session
from app.deps import get_scryfall
from app.main import app
from app.scryfall import ScryfallClient


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
    scryfall_json("Sol Ring", "id-sol", "cmm", eur="1.50", eur_foil="4.00"),
    scryfall_json("Sol Ring", "id-sol-lea", "lea", eur="900.00"),
    scryfall_json("Lim-Dûl's Vault", "id-vault", "all", eur="12.34"),
    scryfall_json("Fire // Ice", "id-fire", "mh2", eur="0.25", eur_foil="0.80"),
    scryfall_json("Black Lotus", "id-lotus", "lea", eur=None),
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
        if request.url.path == "/cards/collection":
            data, missing = [], []
            for identifier in json.loads(request.content)["identifiers"]:
                card = self._find(identifier)
                (data if card else missing).append(card or identifier)
            return httpx.Response(200, json={"data": data, "not_found": missing})
        card = self._find({"id": request.url.path.rsplit("/", 1)[-1]})
        return httpx.Response(200, json=card) if card else not_found


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
def client(scryfall):
    app.dependency_overrides[get_scryfall] = lambda: scryfall
    yield TestClient(app)
    app.dependency_overrides.clear()
