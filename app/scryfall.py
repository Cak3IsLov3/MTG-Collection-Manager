"""Async Scryfall API client (https://scryfall.com/docs/api)."""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from urllib.parse import quote

import httpx

from app.config import get_settings
from app.rate_limit import RateLimiter

logger = logging.getLogger(__name__)

BASE_URL = "https://api.scryfall.com"
COLLECTION_BATCH_SIZE = 75  # hard limit of POST /cards/collection
SEARCH_NAMES_PER_QUERY = 8  # card names looked up in one /cards/search query
MAX_SEARCH_PAGES = 20
MAX_RETRIES = 3
MAX_RETRY_DELAY = 90.0
# Scryfall allows far fewer requests on these endpoints than on the rest of the API.
SLOW_PATHS = ("/cards/collection", "/cards/named", "/cards/search")


class ScryfallError(Exception):
    pass


class CardNotFound(ScryfallError):
    pass


class InvalidCardRequest(ScryfallError):
    """The lookup itself makes no sense (e.g. a card number without a set); nothing was sent."""


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
    # Filled in when this printing has no price and the price of another printing is used instead.
    eur_from_set: str | None = None
    eur_foil_from_set: str | None = None
    # Colour identity as letters in WUBRG order; "" is colourless.
    colors: str | None = None
    type_line: str = ""  # e.g. "Legendary Creature — Robot"
    cmc: float | None = None  # mana value
    oracle_text: str = ""  # rules text; of both faces for a double-faced card
    # The tokens and emblems the card makes: [(Scryfall id, name)]. Scryfall lists an emblem as a
    # "combo_piece" of its planeswalker, so it is told apart by its type.
    token_parts: list[tuple[str, str]] = field(default_factory=list)

    @classmethod
    def from_json(cls, data: dict) -> "ScryfallCard":
        # Double-faced cards have no top-level image, only one per face.
        images = data.get("image_uris") or next(
            (f["image_uris"] for f in data.get("card_faces") or [] if f.get("image_uris")), {}
        )
        prices = data.get("prices") or {}
        identity = data.get("color_identity")
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
            colors=None if identity is None else "".join(c for c in "WUBRG" if c in identity),
            type_line=data.get("type_line") or "",
            cmc=data.get("cmc"),
            oracle_text=data.get("oracle_text")
            or "\n".join(face.get("oracle_text") or "" for face in data.get("card_faces") or []),
            token_parts=[
                (part["id"], part["name"]) for part in data.get("all_parts") or []
                if (part.get("component") == "token" or (part.get("type_line") or "").startswith("Emblem"))
                and part.get("id") and part.get("id") != data["id"]
            ],
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
        slow_interval: float | None = None,
    ) -> None:
        settings = get_settings()
        if min_interval is None:
            min_interval = settings.scryfall_min_interval
        if slow_interval is None:
            slow_interval = max(min_interval, settings.scryfall_slow_interval) if min_interval else 0.0
        self._limiter = RateLimiter(min_interval)
        self._slow_limiter = RateLimiter(slow_interval)
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
            await (self._slow_limiter if path in SLOW_PATHS else self._limiter).wait()
            try:
                response = await self._client.request(method, path, **kwargs)
            except httpx.HTTPError as exc:
                raise ScryfallError(f"Scryfall request failed: {exc}") from exc
            if response.status_code == 429 and attempt < MAX_RETRIES:
                delay = _to_float(response.headers.get("Retry-After"))
                # Hitting the limit again right away makes Scryfall extend the block, so wait it out.
                delay = 1.0 if delay is None else delay
                logger.warning("Scryfall rate limit hit, waiting %.0f s", delay)
                await asyncio.sleep(min(max(delay, 1.0), MAX_RETRY_DELAY))
                continue
            if response.status_code == 404:
                raise CardNotFound(f"Card not found on Scryfall ({path})")
            if response.status_code >= 400:
                raise ScryfallError(f"Scryfall returned HTTP {response.status_code}")
            return response.json()
        raise ScryfallError("Scryfall rate limit exceeded")  # pragma: no cover

    async def autocomplete(self, query: str, kind: str = "card") -> list[str]:
        """Card names that start with or contain `query`, for typing ahead (at most 20).

        kind "token" also gives tokens and emblems; "commander" only cards that can be a commander."""
        query = query.strip()
        if len(query) < 2:
            return []
        if kind == "commander":
            if '"' in query:
                return []
            results = await self.search(f'is:commander name:"{query}" game:paper', unique="cards", order="edhrec")
            return [data["name"] for data in results[:20] if data.get("name")]
        params = {"q": query, **({"include_extras": "true"} if kind == "token" else {})}
        return list((await self._request("GET", "/cards/autocomplete", params=params)).get("data") or [])[:20]

    async def get_by_id(self, scryfall_id: str) -> ScryfallCard:
        return ScryfallCard.from_json(await self._request("GET", f"/cards/{scryfall_id}"))

    async def get_by_number(self, set_code: str, collector_number: str) -> ScryfallCard:
        """One exact printing, e.g. ("cmm", "410"); the way to tell apart versions within a set."""
        path = f"/cards/{quote(set_code.strip().lower(), safe='')}/{quote(collector_number.strip(), safe='')}"
        try:
            return ScryfallCard.from_json(await self._request("GET", path))
        except CardNotFound:
            raise CardNotFound(f"No card #{collector_number.strip()} in set {set_code.strip().upper()}") from None

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

    async def search(self, query: str, **params) -> list[dict]:
        """All cards matching a Scryfall search query (raw JSON), following the pages."""
        results: list[dict] = []
        for page in range(1, MAX_SEARCH_PAGES + 1):
            try:
                data = await self._request("GET", "/cards/search", params={"q": query, "page": page, **params})
            except CardNotFound:  # Scryfall answers 404 when nothing matches
                break
            results.extend(data.get("data", []))
            if not data.get("has_more"):
                break
        return results

    async def printings(self, name: str) -> list[ScryfallCard]:
        """Every paper printing of a card, newest first."""
        if '"' in name:
            return []  # cannot be quoted in an exact-name search
        results = await self.search(f'!"{name}" game:paper', unique="prints", order="released", dir="desc")
        return [ScryfallCard.from_json(data) for data in results if name_key(data.get("name", "")) == name_key(name)]

    async def fill_missing_prices(self, cards: list[ScryfallCard], foil_ids: frozenset[str] | set[str] = frozenset()) -> int:
        """Give cards without a price the price of another printing with the same name.

        Some printings have no price on Scryfall at all. For those the cheapest
        printing that does have one is used, and the set it came from is noted
        on the card. A missing foil price is only looked up for the ids in
        `foil_ids`. Returns the number of prices filled in; never raises.
        """
        wanted = [
            card
            for card in cards
            if card.price_eur is None or (card.scryfall_id in foil_ids and card.price_eur_foil is None)
        ]
        names = sorted({card.name for card in wanted if '"' not in card.name})
        cheapest: dict[tuple[str, str], tuple[float, str]] = {}  # (name key, "eur" | "eur_foil") -> (price, set)
        try:
            for start in range(0, len(names), SEARCH_NAMES_PER_QUERY):
                batch = names[start : start + SEARCH_NAMES_PER_QUERY]
                query = "(" + " or ".join(f'!"{name}"' for name in batch) + ") game:paper"
                for data in await self.search(query, unique="prints"):
                    for finish in ("eur", "eur_foil"):
                        price = _to_float((data.get("prices") or {}).get(finish))
                        key = (name_key(data.get("name", "")), finish)
                        if price is not None and (key not in cheapest or price < cheapest[key][0]):
                            cheapest[key] = (price, data.get("set", ""))
        except ScryfallError as exc:
            logger.warning("Could not look up prices of other printings: %s", exc)

        filled = 0
        for card in wanted:
            regular = cheapest.get((name_key(card.name), "eur"))
            if card.price_eur is None and regular:
                card.price_eur, card.eur_from_set = regular
                filled += 1
            foil = cheapest.get((name_key(card.name), "eur_foil"))
            if card.scryfall_id in foil_ids and card.price_eur_foil is None and foil:
                card.price_eur_foil, card.eur_foil_from_set = foil
                filled += 1
        return filled

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
