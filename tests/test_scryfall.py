import time

import httpx
import pytest

from app.rate_limit import RateLimiter
from app.scryfall import CardNotFound, CardRef, ScryfallCard, ScryfallClient, ScryfallError
from tests.conftest import scryfall_json


def test_from_json_parses_prices_and_image():
    card = ScryfallCard.from_json(scryfall_json("Sol Ring", "abc", "cmm", eur="1.50", eur_foil="4.00"))
    assert (card.name, card.set_code, card.price_eur, card.price_eur_foil) == ("Sol Ring", "cmm", 1.5, 4.0)
    assert card.image_url == "https://img.example/abc.jpg"
    assert card.cardmarket_url.startswith("https://www.cardmarket.com/")


def test_from_json_handles_null_prices():
    card = ScryfallCard.from_json(scryfall_json("Black Lotus", "abc", eur=None))
    assert card.price_eur is None and card.price_eur_foil is None


def test_from_json_handles_missing_fields():
    card = ScryfallCard.from_json({"id": "abc", "name": "Mystery"})
    assert card.image_url is None and card.price_eur is None and card.cardmarket_url is None


def test_from_json_uses_front_face_image_for_double_faced_cards():
    data = scryfall_json("Delver of Secrets // Insectile Aberration", "abc")
    del data["image_uris"]
    data["card_faces"] = [{"image_uris": {"normal": "front.jpg"}}, {"image_uris": {"normal": "back.jpg"}}]
    assert ScryfallCard.from_json(data).image_url == "front.jpg"


@pytest.mark.parametrize("name", ["Lim-Dûl's Vault", "Fire // Ice"])
async def test_get_by_name_with_special_characters(scryfall, fake_scryfall, name):
    card = await scryfall.get_by_name(name)
    assert card.name == name
    # The name must survive URL encoding untouched.
    assert fake_scryfall.requests[-1].url.params["exact"] == name


async def test_get_by_name_respects_set(scryfall):
    assert (await scryfall.get_by_name("Sol Ring", "LEA")).scryfall_id == "id-sol-lea"


async def test_get_by_name_falls_back_to_fuzzy(scryfall, fake_scryfall):
    card = await scryfall.get_by_name("Lim-Dûl")
    assert card.name == "Lim-Dûl's Vault"
    assert [("exact" in r.url.params) for r in fake_scryfall.requests] == [True, False]


async def test_get_by_name_not_found(scryfall):
    with pytest.raises(CardNotFound):
        await scryfall.get_by_name("Definitely Not A Card")


async def test_sends_identifying_headers(scryfall, fake_scryfall):
    await scryfall.get_by_id("id-sol")
    headers = fake_scryfall.requests[0].headers
    assert headers["user-agent"].startswith("MTGCollectionManager")
    assert headers["accept"] == "application/json"


async def test_get_collection_batches_per_75(scryfall, fake_scryfall):
    identifiers = [{"id": f"unknown-{i}"} for i in range(160)] + [{"id": "id-sol"}]
    found, not_found = await scryfall.get_collection(identifiers)
    assert [c.scryfall_id for c in found] == ["id-sol"]
    assert len(not_found) == 160
    assert len(fake_scryfall.requests) == 3


async def test_resolve_keeps_order_and_marks_unknown(scryfall):
    refs = [
        CardRef(name="Fire"),  # one face of a split card
        CardRef(name="Nope"),
        CardRef(name="Sol Ring", set_code="LEA"),
        CardRef(name="whatever", scryfall_id="id-vault"),
    ]
    resolved = await scryfall.resolve(refs)
    assert [c.scryfall_id if c else None for c in resolved] == ["id-fire", None, "id-sol-lea", "id-vault"]


async def test_resolve_retries_by_name_when_set_is_unknown(scryfall):
    resolved = await scryfall.resolve([CardRef(name="Lim-Dûl's Vault", set_code="zzz")])
    assert resolved[0].scryfall_id == "id-vault"


async def test_retries_after_429():
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, json=scryfall_json("Sol Ring", "id-sol"))

    client = ScryfallClient(transport=httpx.MockTransport(handler), min_interval=0)
    assert (await client.get_by_id("id-sol")).name == "Sol Ring"
    assert len(calls) == 2


async def test_server_error_raises_scryfall_error():
    client = ScryfallClient(transport=httpx.MockTransport(lambda r: httpx.Response(500)), min_interval=0)
    with pytest.raises(ScryfallError):
        await client.get_by_id("id-sol")


async def test_rate_limiter_spaces_out_calls():
    limiter = RateLimiter(min_interval=0.05)
    start = time.monotonic()
    for _ in range(3):
        await limiter.wait()
    assert time.monotonic() - start >= 0.09  # 2 gaps of 50 ms


async def test_bulk_and_lookup_endpoints_use_the_slow_lane(fake_scryfall):
    client = ScryfallClient(transport=httpx.MockTransport(fake_scryfall), min_interval=0.01, slow_interval=0.05)
    start = time.monotonic()
    for _ in range(3):
        await client.get_by_id("id-sol")  # fast lane
    fast = time.monotonic() - start
    for _ in range(3):
        await client.get_collection([{"id": "id-sol"}])
    slow = time.monotonic() - start - fast
    assert fast < 0.09 <= slow
