"""Async Scryfall API client (https://scryfall.com/docs/api)."""

import asyncio
import json
import logging
from dataclasses import dataclass

import httpx

from app.config import get_settings
from app.rate_limit import RateLimiter

logger = logging.getLogger(__name__)

BASE_URL = "https://api.scryfall.com"
COLLECTION_BATCH_SIZE = 75  # hard limit of POST /cards/collection
MAX_RETRIES = 2


class ScryfallError(Exception):
    pass


class CardNotFound(ScryfallError):
    pass


def _to_float(value) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def name_key(name: str) -> str:
    return name.strip().casefold()


@dataclass
class CardRef:
    """What we know about a card before Scryfall has resolved it."""

    name: str
    set_code: str | None = None
    collector_number: str | None = None
    scryfall_id: str | None = None

    def identifier(self) -> dict:
        if self.scryfall_id:
            return {"id": self.scryfall_id}
        if self.set_code and self.collector_number:
            return {"set": self.set_code.lower(), "collector_number": self.collector_number}
        if self.set_code:
            return {"name": self.name, "set": self.set_code.lower()}
        return {"name": self.name}


@dataclass
class ScryfallCard:
    scryfall_id: str
    name: str
    set_code: str
    set_name: str
    collector_number: str | None
    image_url: str | None
    price_eur: float | None
    price_eur_foil: float | None
    cardmarket_url: str | None

    @classmethod
    def from_json(cls, data: dict) -> "ScryfallCard":
        # Double-faced cards have no top-level image, only one per face.
        images = data.get("image_uris") or next(
            (f["image_uris"] for f in data.get("card_faces") or [] if f.get("image_uris")), {}
        )
        prices = data.get("prices") or {}
        return cls(
            scryfall_id=data["id"],
            name=data["name"],
            set_code=data.get("set", ""),
            set_name=data.get("set_name", ""),
            collector_number=data.get("collector_number"),
            image_url=images.get("normal") or images.get("large") or images.get("small"),
            price_eur=_to_float(prices.get("eur")),
            price_eur_foil=_to_float(prices.get("eur_foil")),
            cardmarket_url=(data.get("purchase_uris") or {}).get("cardmarket"),
        )


class _CardIndex:
    """Looks up resolved cards by id, set+number, name+set or name."""

    def __init__(self) -> None:
        self._by_key: dict[tuple, ScryfallCard] = {}

    def add(self, cards: list[ScryfallCard]) -> None:
        for card in cards:
            self._by_key.setdefault(("id", card.scryfall_id), card)
            self._by_key.setdefault(("num", card.set_code, card.collector_number), card)
            # "Fire // Ice" should also be found as "Fire" or "Ice".
            for name in {card.name, *card.name.split(" // ")}:
                self._by_key.setdefault(("name", name_key(name), card.set_code), card)
                self._by_key.setdefault(("name", name_key(name)), card)

    def lookup(self, ref: CardRef) -> ScryfallCard | None:
        set_code = ref.set_code.lower() if ref.set_code else None
        keys = [
            ("id", ref.scryfall_id) if ref.scryfall_id else None,
            ("num", set_code, ref.collector_number) if set_code and ref.collector_number else None,
            ("name", name_key(ref.name), set_code) if set_code else None,
            ("name", name_key(ref.name)),
        ]
        for key in keys:
            if key and key in self._by_key:
                return self._by_key[key]
        return None


class ScryfallClient:
    def __init__(
        self,
        transport: httpx.AsyncBaseTransport | None = None,
        min_interval: float | None = None,
    ) -> None:
        settings = get_settings()
        if min_interval is None:
            min_interval = settings.scryfall_min_interval
        self._limiter = RateLimiter(min_interval)
        self._client = httpx.AsyncClient(
            base_url=BASE_URL,
            headers={"User-Agent": settings.user_agent, "Accept": "application/json"},
            timeout=15.0,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        for attempt in range(MAX_RETRIES + 1):
            await self._limiter.wait()
            try:
                response = await self._client.request(method, path, **kwargs)
            except httpx.HTTPError as exc:
                raise ScryfallError(f"Scryfall request failed: {exc}") from exc
            if response.status_code == 429 and attempt < MAX_RETRIES:
                delay = _to_float(response.headers.get("Retry-After"))
                await asyncio.sleep(1.0 if delay is None else delay)
                continue
            if response.status_code == 404:
                raise CardNotFound(f"Card not found on Scryfall ({path})")
            if response.status_code >= 400:
                raise ScryfallError(f"Scryfall returned HTTP {response.status_code}")
            return response.json()
        raise ScryfallError("Scryfall rate limit exceeded")  # pragma: no cover

    async def get_by_id(self, scryfall_id: str) -> ScryfallCard:
        return ScryfallCard.from_json(await self._request("GET", f"/cards/{scryfall_id}"))

    async def get_by_name(self, name: str, set_code: str | None = None) -> ScryfallCard:
        """Exact name match first, fuzzy as a fallback for typos."""
        extra = {"set": set_code.lower()} if set_code else {}
        try:
            data = await self._request("GET", "/cards/named", params={"exact": name, **extra})
        except CardNotFound:
            try:
                data = await self._request("GET", "/cards/named", params={"fuzzy": name, **extra})
            except CardNotFound:
                raise CardNotFound(f"Card not found: {name}") from None
        return ScryfallCard.from_json(data)

    async def get_collection(self, identifiers: list[dict]) -> tuple[list[ScryfallCard], list[dict]]:
        """Batch lookup; returns (found cards, identifiers Scryfall could not find)."""
        unique = list({json.dumps(i, sort_keys=True): i for i in identifiers}.values())
        found: list[ScryfallCard] = []
        not_found: list[dict] = []
        for start in range(0, len(unique), COLLECTION_BATCH_SIZE):
            batch = unique[start : start + COLLECTION_BATCH_SIZE]
            data = await self._request("POST", "/cards/collection", json={"identifiers": batch})
            found.extend(ScryfallCard.from_json(card) for card in data.get("data", []))
            not_found.extend(data.get("not_found", []))
        return found, not_found

    async def resolve(self, refs: list[CardRef]) -> list[ScryfallCard | None]:
        """Resolve refs in bulk. Result is aligned with `refs`; unknown cards are None."""
        if not refs:
            return []
        index = _CardIndex()
        found, _ = await self.get_collection([ref.identifier() for ref in refs])
        index.add(found)
        results = [index.lookup(ref) for ref in refs]

        # Second chance by name only: export tools sometimes use set codes Scryfall doesn't know.
        retry = [
            ref
            for ref, card in zip(refs, results)
            if card is None and ref.name and ref.identifier() != {"name": ref.name}
        ]
        if retry:
            found, _ = await self.get_collection([{"name": ref.name} for ref in retry])
            index.add(found)
            results = [card or index.lookup(CardRef(name=ref.name)) for ref, card in zip(refs, results)]
        return results
