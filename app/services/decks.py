"""Decks: which cards of a deck do you own, which are tied up in other decks, and which are missing."""

import re
from dataclasses import dataclass, field

import httpx
from sqlmodel import Session, select

from app.config import get_settings
from app.csv_import import CsvFormatError, parse_collection_csv
from app.models import Card, Deck, DeckEntry, WishlistEntry
from app.scryfall import CardRef, ScryfallClient, name_key
from app.services.collection import CollectionRow, collection_rows
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
    on_wishlist: bool
    # The printings of this card in the collection, so you know which copies to pull.
    printings: list[CollectionRow] = field(default_factory=list)
    # Copies that saved decks with a higher priority already claim, and the names of those decks.
    reserved: int = 0
    used_by: list[str] = field(default_factory=list)

    @property
    def owned(self) -> int:
        return sum(row.entry.quantity for row in self.printings)

    @property
    def available(self) -> int:
        """Owned copies that are not tied up in another deck."""
        return max(0, self.owned - self.reserved)

    @property
    def usable(self) -> int:
        """Copies this deck can actually use."""
        return min(self.available, self.needed)

    @property
    def missing(self) -> int:
        return self.needed - self.usable


@dataclass
class DeckSummary:
    deck: Deck
    needed: int
    usable: int

    @property
    def complete(self) -> bool:
        return self.usable >= self.needed

    @property
    def percent(self) -> int:
        return round(self.usable / self.needed * 100) if self.needed else 0


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


def parse_deck_file(content: bytes) -> list[DeckCard]:
    """An uploaded deck: a CSV export (Archidekt/Moxfield) or a plain text decklist."""
    try:
        rows = parse_collection_csv(content).rows
    except CsvFormatError:
        return parse_decklist(content.decode("utf-8-sig", errors="replace"))
    return [DeckCard(name=row.name, quantity=row.quantity) for row in rows]


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


class _CardPool:
    """The collection, shared out over the saved decks.

    Every physical copy can only be in one deck. Saved decks claim their cards
    in the order they were created; whatever is left is available to newer
    decks and to a deck you are only checking.
    """

    def __init__(self, session: Session) -> None:
        self.printings: dict[str, list[CollectionRow]] = {}
        self._alias: dict[str, str] = {}  # "fire" -> "fire // ice"
        for row in collection_rows(session):
            full = name_key(row.card.name)
            self.printings.setdefault(full, []).append(row)
            for name in _names(row.card.name):
                self._alias.setdefault(name, full)

        self.wished: set[str] = set()
        for card in session.exec(
            select(Card).join(WishlistEntry, WishlistEntry.card_id == Card.id).where(WishlistEntry.active == True)  # noqa: E712
        ):
            self.wished |= _names(card.name)

        self.decks = list(session.exec(select(Deck).order_by(Deck.id)))
        self.entries: dict[int, list[DeckCard]] = {deck.id: [] for deck in self.decks}
        for entry in session.exec(select(DeckEntry).order_by(DeckEntry.id)):
            self.entries.setdefault(entry.deck_id, []).append(DeckCard(entry.name, entry.quantity))

        remaining = {key: sum(row.entry.quantity for row in rows) for key, rows in self.printings.items()}
        self.claimed: dict[int, dict[str, int]] = {}
        for deck in self.decks:
            claims = {}
            for key, card in self._needs(self.entries[deck.id]).items():
                take = min(card.quantity, remaining.get(key, 0))
                if take:
                    claims[key] = take
                    remaining[key] -= take
            self.claimed[deck.id] = claims

    def _key(self, name: str) -> str:
        key = name_key(name)
        return self._alias.get(key, key)

    def _needs(self, cards: list[DeckCard]) -> dict[str, DeckCard]:
        needed: dict[str, DeckCard] = {}
        for card in cards:
            key = self._key(card.name)
            if key in needed:
                needed[key].quantity += card.quantity
            else:
                needed[key] = DeckCard(card.name, card.quantity)
        return needed

    def rows(self, cards: list[DeckCard], deck_id: int | None = None) -> list[DeckRow]:
        """Have/missing overview. `deck_id` is the saved deck being viewed, if any."""
        ahead = [deck for deck in self.decks if deck_id is None or deck.id < deck_id]
        rows = []
        for key, card in self._needs(cards).items():
            users = [(deck.name, self.claimed[deck.id].get(key, 0)) for deck in ahead]
            rows.append(
                DeckRow(
                    name=card.name,
                    needed=card.quantity,
                    on_wishlist=key in self.wished or name_key(card.name) in self.wished,
                    printings=self.printings.get(key, []),
                    reserved=sum(count for _, count in users),
                    used_by=[name for name, count in users if count],
                )
            )
        return sorted(rows, key=lambda row: name_key(row.name))


def check_deck(session: Session, cards: list[DeckCard], deck_id: int | None = None) -> list[DeckRow]:
    """Compare a deck with the collection by card name (any printing counts).

    Copies that saved decks already use are not counted again. Pass `deck_id`
    when the cards belong to a saved deck, so it is not competing with itself.
    """
    return _CardPool(session).rows(cards, deck_id)


def save_deck(session: Session, name: str, cards: list[DeckCard]) -> Deck:
    deck = Deck(name=name.strip() or "Untitled deck")
    session.add(deck)
    session.flush()
    merged: dict[str, DeckCard] = {}
    for card in cards:
        key = name_key(card.name)
        if key in merged:
            merged[key].quantity += card.quantity
        else:
            merged[key] = DeckCard(card.name.strip(), card.quantity)
    for card in merged.values():
        session.add(DeckEntry(deck_id=deck.id, name=card.name, quantity=card.quantity))
    session.flush()
    return deck


def delete_deck(session: Session, deck: Deck) -> None:
    """Remove a saved deck; its cards become available to other decks again."""
    for entry in session.exec(select(DeckEntry).where(DeckEntry.deck_id == deck.id)).all():
        session.delete(entry)
    session.delete(deck)
    session.flush()


def saved_deck_rows(session: Session, deck: Deck) -> list[DeckRow]:
    pool = _CardPool(session)
    return pool.rows(pool.entries.get(deck.id, []), deck.id)


def deck_summaries(session: Session) -> list[DeckSummary]:
    pool = _CardPool(session)
    summaries = []
    for deck in pool.decks:
        rows = pool.rows(pool.entries[deck.id], deck.id)
        summaries.append(DeckSummary(deck, sum(r.needed for r in rows), sum(r.usable for r in rows)))
    return summaries

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
