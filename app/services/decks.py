"""Decks: which cards of a deck do you own, which are tied up in other decks, and which are missing."""

import re
from dataclasses import dataclass, field

import httpx
from sqlmodel import Session, select

from app.config import get_settings
from app.csv_import import CsvFormatError, parse_collection_csv
from app.models import Card, CollectionEntry, Deck, DeckEntry, DeckPick, WishlistEntry
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
    entry_id: int | None = None  # set for the cards of a saved deck


@dataclass
class DeckRow:
    name: str
    needed: int
    on_wishlist: bool
    # Every printing of this card in the collection.
    printings: list[CollectionRow] = field(default_factory=list)
    # The copies this deck uses: (printing, number of copies).
    picks: list[tuple[CollectionRow, int]] = field(default_factory=list)
    # Copies that other saved decks use, and the names of those decks.
    reserved: int = 0
    used_by: list[str] = field(default_factory=list)
    # Copies per printing (collection entry id) this deck could use: its own plus the unused ones.
    free: dict[int, int] = field(default_factory=dict)
    entry_id: int | None = None
    pinned_id: int | None = None  # collection entry id the user chose, if any

    @property
    def owned(self) -> int:
        return sum(row.entry.quantity for row in self.printings)

    @property
    def available(self) -> int:
        """Owned copies that are not tied up in another deck."""
        return max(0, self.owned - self.reserved)

    @property
    def usable(self) -> int:
        """Copies this deck actually has."""
        return sum(count for _, count in self.picks)

    @property
    def missing(self) -> int:
        return self.needed - self.usable

    @property
    def value(self) -> float:
        return round(sum((row.unit_price or 0.0) * count for row, count in self.picks), 2)


@dataclass
class DeckSummary:
    deck: Deck
    needed: int
    usable: int
    value: float = 0.0  # what the copies in this deck are worth

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


@dataclass
class _Need:
    name: str
    quantity: int
    entry_id: int | None = None
    pin: int | None = None


class _CardPool:
    """The collection, shared out over the saved decks.

    Every physical copy can only be in one deck. Printings a user chose for a
    deck are handed out first. After that saved decks claim what they still
    need in the order they were created, cheapest printing first. Whatever is
    left is available to a deck you are only checking.
    """

    def __init__(self, session: Session) -> None:
        self.printings: dict[str, list[CollectionRow]] = {}
        self._alias: dict[str, str] = {}  # "fire" -> "fire // ice"
        for row in collection_rows(session):
            full = name_key(row.card.name)
            self.printings.setdefault(full, []).append(row)
            for name in _names(row.card.name):
                self._alias.setdefault(name, full)
        for rows in self.printings.values():
            rows.sort(key=lambda row: (row.unit_price is None, row.unit_price or 0.0))

        self.wished: set[str] = set()
        for card in session.exec(
            select(Card).join(WishlistEntry, WishlistEntry.card_id == Card.id).where(WishlistEntry.active == True)  # noqa: E712
        ):
            self.wished |= _names(card.name)

        pins = {pick.deck_entry_id: pick.collection_entry_id for pick in session.exec(select(DeckPick))}
        self.decks = list(session.exec(select(Deck).order_by(Deck.id)))
        cards: dict[int, list[DeckCard]] = {deck.id: [] for deck in self.decks}
        for entry in session.exec(select(DeckEntry).order_by(DeckEntry.id)):
            cards.setdefault(entry.deck_id, []).append(DeckCard(entry.name, entry.quantity, entry.id))
        self.needs = {deck.id: self._needs(cards[deck.id], pins) for deck in self.decks}

        # claims[deck id][card key][collection entry id] = copies
        self.remaining = {row.entry.id: row.entry.quantity for rows in self.printings.values() for row in rows}
        self.claims: dict[int, dict[str, dict[int, int]]] = {deck.id: {} for deck in self.decks}
        for deck in self.decks:  # chosen printings first
            for key, need in self.needs[deck.id].items():
                if need.pin is not None:
                    self._take([need.pin], need.quantity, self.remaining, self.claims[deck.id].setdefault(key, {}))
        for deck in self.decks:  # then whatever is still needed
            for key, need in self.needs[deck.id].items():
                claimed = self.claims[deck.id].setdefault(key, {})
                self._take(self._ids(key), need.quantity - sum(claimed.values()), self.remaining, claimed)

    def _key(self, name: str) -> str:
        key = name_key(name)
        return self._alias.get(key, key)

    def _ids(self, key: str) -> list[int]:
        return [row.entry.id for row in self.printings.get(key, [])]

    @staticmethod
    def _take(ids: list[int], wanted: int, remaining: dict[int, int], claimed: dict[int, int]) -> None:
        for entry_id in ids:
            take = min(wanted, remaining.get(entry_id, 0))
            if take > 0:
                claimed[entry_id] = claimed.get(entry_id, 0) + take
                remaining[entry_id] -= take
                wanted -= take

    def _needs(self, cards: list[DeckCard], pins: dict[int, int] | None = None) -> dict[str, _Need]:
        needs: dict[str, _Need] = {}
        for card in cards:
            key = self._key(card.name)
            need = needs.setdefault(key, _Need(card.name, 0, card.entry_id))
            need.quantity += card.quantity
            pin = (pins or {}).get(card.entry_id)
            # A chosen printing only counts while it is still in the collection as this card.
            if need.pin is None and pin in self._ids(key):
                need.pin = pin
        return needs

    def rows(self, cards: list[DeckCard] | None = None, deck_id: int | None = None) -> list[DeckRow]:
        """Have/missing overview for a saved deck (`deck_id`) or for loose `cards`."""
        needs = self.needs[deck_id] if deck_id is not None else self._needs(cards or [])
        leftover = dict(self.remaining)
        rows = []
        for key, need in needs.items():
            if deck_id is not None:
                mine = self.claims[deck_id].get(key, {})
            else:
                mine = {}
                self._take(self._ids(key), need.quantity, leftover, mine)
            others = [
                (deck.name, sum(self.claims[deck.id].get(key, {}).values()))
                for deck in self.decks
                if deck.id != deck_id
            ]
            printings = self.printings.get(key, [])
            rows.append(
                DeckRow(
                    name=need.name,
                    needed=need.quantity,
                    on_wishlist=key in self.wished or name_key(need.name) in self.wished,
                    printings=printings,
                    picks=[(row, mine[row.entry.id]) for row in printings if mine.get(row.entry.id)],
                    reserved=sum(count for _, count in others),
                    used_by=[name for name, count in others if count],
                    free={row.entry.id: self.remaining[row.entry.id] + mine.get(row.entry.id, 0) for row in printings}
                    if deck_id is not None
                    else {},
                    entry_id=need.entry_id,
                    pinned_id=need.pin,
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
        for pick in session.exec(select(DeckPick).where(DeckPick.deck_entry_id == entry.id)).all():
            session.delete(pick)
        session.delete(entry)
    session.delete(deck)
    session.flush()


def choose_printing(session: Session, deck: Deck, entry_id: int, collection_entry_id: int | None) -> None:
    """Pin the printing a deck uses for one of its cards; None hands the choice back to the app."""
    entry = session.get(DeckEntry, entry_id)
    if entry is None or entry.deck_id != deck.id:
        raise DeckError("That card is not in this deck")
    for pick in session.exec(select(DeckPick).where(DeckPick.deck_entry_id == entry_id)).all():
        session.delete(pick)
    session.flush()
    if collection_entry_id is None:
        return
    owned = session.get(CollectionEntry, collection_entry_id)
    card = session.get(Card, owned.card_id) if owned is not None else None
    if card is None or not _names(card.name) & _names(entry.name):
        raise DeckError("That printing is not in your collection")
    session.add(DeckPick(deck_entry_id=entry_id, collection_entry_id=collection_entry_id))
    session.flush()

def saved_deck_rows(session: Session, deck: Deck) -> list[DeckRow]:
    return _CardPool(session).rows(deck_id=deck.id)


def deck_summaries(session: Session) -> list[DeckSummary]:
    pool = _CardPool(session)
    summaries = []
    for deck in pool.decks:
        rows = pool.rows(deck_id=deck.id)
        summaries.append(
            DeckSummary(
                deck,
                needed=sum(row.needed for row in rows),
                usable=sum(row.usable for row in rows),
                value=round(sum(row.value for row in rows), 2),
            )
        )
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
