"""Deck view: which cards of a deck do you own, and which are still missing."""

import re
from dataclasses import dataclass

import httpx
from sqlmodel import Session, select

from app.config import get_settings
from app.models import Card, CollectionEntry, WishlistEntry
from app.scryfall import CardRef, ScryfallClient, name_key
from app.services.pricing import store_scryfall_card
from app.services.wishlist import add_to_wishlist

ARCHIDEKT_API = "https://archidekt.com/api/decks/{deck_id}/"
SKIPPED_CATEGORIES = {"maybeboard"}

_ARCHIDEKT_URL = re.compile(r"archidekt\.com/(?:api/)?decks/(\d+)", re.IGNORECASE)
# "[Ramp]" categories, "^Have,#37d67a^" colour labels and "*F*" foil markers
_ANNOTATIONS = re.compile(r"\s*(\[[^\]]*\]|\^[^^]*\^|\*[A-Z]\*)")
_LINE = re.compile(r"^(?P<qty>\d+)\s*x?\s+(?P<name>.+?)(?:\s+\((?P<set>[A-Za-z0-9]{2,6})\)(?:\s+\S+)?)?$")


class DeckError(Exception):
    pass


@dataclass
class DeckCard:
    name: str
    quantity: int = 1


@dataclass
class DeckRow:
    name: str
    needed: int
    owned: int
    on_wishlist: bool

    @property
    def missing(self) -> int:
        return max(0, self.needed - self.owned)


def parse_decklist(text: str) -> list[DeckCard]:
    """Parse '1 Sol Ring' / '1x Sol Ring (cmm) 400 [Ramp]' lines; headers and comments are skipped."""
    cards = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "//")):
            continue
        match = _LINE.match(_ANNOTATIONS.sub("", line).strip())
        if match and int(match["qty"]) > 0:
            cards.append(DeckCard(name=match["name"].strip(), quantity=int(match["qty"])))
    return cards


def archidekt_deck_id(source: str) -> int | None:
    match = _ARCHIDEKT_URL.search(source)
    return int(match.group(1)) if match else None


def parse_archidekt_deck(data: dict) -> tuple[str, list[DeckCard]]:
    cards = []
    for item in data.get("cards") or []:
        categories = {str(c).casefold() for c in item.get("categories") or []}
        name = ((item.get("card") or {}).get("oracleCard") or {}).get("name")
        if not name or categories & SKIPPED_CATEGORIES:
            continue
        cards.append(DeckCard(name=name, quantity=int(item.get("quantity") or 1)))
    return data.get("name") or "Archidekt deck", cards


async def fetch_archidekt_deck(
    deck_id: int, transport: httpx.AsyncBaseTransport | None = None
) -> tuple[str, list[DeckCard]]:
    headers = {"User-Agent": get_settings().user_agent, "Accept": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=15.0, headers=headers, transport=transport) as client:
            response = await client.get(ARCHIDEKT_API.format(deck_id=deck_id))
    except httpx.HTTPError as exc:
        raise DeckError(f"Could not reach Archidekt: {exc}") from exc
    if response.status_code == 404:
        raise DeckError("Deck not found on Archidekt (is it private?)")
    if response.status_code >= 400:
        raise DeckError(f"Archidekt returned HTTP {response.status_code}")
    try:
        return parse_archidekt_deck(response.json())
    except ValueError as exc:
        raise DeckError("Archidekt returned an unexpected response") from exc


def _names(full_name: str) -> set[str]:
    """A split/double-faced card answers to its full name and to each face."""
    return {name_key(part) for part in (full_name, *full_name.split(" // "))}


def check_deck(session: Session, cards: list[DeckCard]) -> list[DeckRow]:
    """Compare a deck with the collection by card name (any printing counts)."""
    owned: dict[str, int] = {}
    for entry, card in session.exec(select(CollectionEntry, Card).join(Card, CollectionEntry.card_id == Card.id)):
        for key in _names(card.name):
            owned[key] = owned.get(key, 0) + entry.quantity
    wished: set[str] = set()
    for card in session.exec(
        select(Card).join(WishlistEntry, WishlistEntry.card_id == Card.id).where(WishlistEntry.active == True)  # noqa: E712
    ):
        wished |= _names(card.name)

    needed: dict[str, DeckCard] = {}
    for card in cards:
        key = name_key(card.name)
        if key in needed:
            needed[key].quantity += card.quantity
        else:
            needed[key] = DeckCard(card.name, card.quantity)
    return [
        DeckRow(name=card.name, needed=card.quantity, owned=owned.get(key, 0), on_wishlist=key in wished)
        for key, card in sorted(needed.items())
    ]


async def add_missing_to_wishlist(
    session: Session, scryfall: ScryfallClient, names: list[str], target_pct: float = 90.0
) -> tuple[int, list[str]]:
    """Put cards on the wishlist with a target of `target_pct`% of today's price.

    Returns (number added, names Scryfall could not find).
    """
    added, not_found = 0, []
    resolved = await scryfall.resolve([CardRef(name=name) for name in names])
    for name, data in zip(names, resolved):
        if data is None:
            not_found.append(name)
            continue
        card = store_scryfall_card(session, data)
        target = round((data.price_eur or 0.0) * target_pct / 100, 2)
        add_to_wishlist(session, card, target, data.cardmarket_url)
        added += 1
    return added, not_found
