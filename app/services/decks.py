"""Decks: which cards of a deck do you own, which are tied up in other decks, and which are missing."""

import random
import re
from dataclasses import dataclass, field

import httpx
from sqlmodel import Session, select

from app.config import get_settings
from app.csv_import import CsvFormatError, parse_collection_csv
from app.models import (
    Card,
    CollectionEntry,
    Deck,
    DeckBasicLand,
    DeckEntry,
    DeckPick,
    DeckSkipped,
    WishlistEntry,
)
from app.scryfall import CardRef, ScryfallClient, name_key
from app.services.collection import CollectionRow, collection_rows, find_card
from app.services.pricing import store_scryfall_card
from app.services.wishlist import add_to_wishlist

ARCHIDEKT_API = "https://archidekt.com/api/decks/{deck_id}/"
SKIPPED_CATEGORIES = {"maybeboard"}
TOKEN_CATEGORIES = {"tokens & extras"}
TOKEN_LAYOUTS = {"token", "double_faced_token", "emblem"}

# What a line in a deck is. Basic lands and tokens are left out of a deck by default.
CARD, BASIC, TOKEN = "card", "basic", "token"
_BASIC_TYPES = ("Plains", "Island", "Swamp", "Mountain", "Forest", "Wastes")
BASIC_LAND_NAMES = [*_BASIC_TYPES, *(f"Snow-Covered {land}" for land in _BASIC_TYPES)]
BASIC_LANDS = {name.casefold(): name for name in BASIC_LAND_NAMES}  # key -> display name

# Commander brackets (https://magic.wizards.com/en/news/announcements/introducing-commander-brackets-beta)
BRACKETS = {1: "Exhibition", 2: "Core", 3: "Upgraded", 4: "Optimized", 5: "cEDH"}
_CATEGORY = re.compile(r"\[([^\]]*)\]")

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
    kind: str = "card"  # CARD, BASIC or TOKEN, when the source says so
    unassigned: bool = False  # the user took their copies out of the deck


@dataclass
class LeftOut:
    """What a deck contains besides the cards that are tracked against the collection."""

    basics: dict[str, int] = field(default_factory=dict)  # "Plains" -> copies
    tokens: int = 0

    @property
    def basic_total(self) -> int:
        return sum(self.basics.values())


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
    # The user took their copies out of this deck: it uses none, so the card counts as missing.
    unassigned: bool = False

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
    basic_lands: int = 0

    @property
    def bracket_name(self) -> str | None:
        return BRACKETS.get(self.deck.bracket)

    @property
    def complete(self) -> bool:
        return self.usable >= self.needed

    @property
    def total_cards(self) -> int:
        """Deck size: the tracked cards plus the basic lands (a Commander deck has 100)."""
        return self.needed + self.basic_lands

    @property
    def total_owned(self) -> int:
        """Cards of the deck you have; basic lands are counted as owned."""
        return self.usable + self.basic_lands

    @property
    def percent(self) -> int:
        return round(self.total_owned / self.total_cards * 100) if self.total_cards else 0


def card_kind(name: str, kind: str = CARD) -> str:
    """Recognise basic lands and tokens by name, for sources that do not say what a card is."""
    if kind != CARD:
        return kind
    if name_key(name) in BASIC_LANDS:
        return BASIC
    if name_key(name).endswith(" token"):
        return TOKEN
    return CARD


def _text_category(line: str) -> tuple[str, bool]:
    """The first '[Category]' on an Archidekt text line and whether it is outside the deck."""
    match = _CATEGORY.search(line)
    if not match:
        return "", False
    first = match.group(1).split(",")[0]
    return re.sub(r"\{[^}]*\}", "", first).strip().casefold(), "{nodeck}" in first.casefold()


def parse_decklist(text: str) -> list[DeckCard]:
    """Parse '1 Sol Ring' / '1x Sol Ring (cmm) 400 [Ramp]' lines; headers and comments are skipped."""
    cards = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "//")):
            continue
        match = _LINE.match(_ANNOTATIONS.sub("", line).strip())
        if not match or int(match["qty"]) <= 0:
            continue
        category, outside_deck = _text_category(line)
        if category in TOKEN_CATEGORIES:
            kind = TOKEN
        elif category in SKIPPED_CATEGORIES or outside_deck:
            continue
        else:
            kind = CARD
        cards.append(DeckCard(name=match["name"].strip(), quantity=int(match["qty"]), kind=kind))
    return cards


def parse_deck_file(content: bytes) -> list[DeckCard]:
    """An uploaded deck: a CSV export (Archidekt/Moxfield) or a plain text decklist."""
    try:
        rows = parse_collection_csv(content).rows
    except CsvFormatError:
        return parse_decklist(content.decode("utf-8-sig", errors="replace"))
    return [DeckCard(name=row.name, quantity=row.quantity) for row in rows]


def select_tracked(
    cards: list[DeckCard], include_basics: bool = False, include_tokens: bool = False
) -> tuple[list[DeckCard], LeftOut]:
    """Leave basic lands and tokens out of the tracked cards unless asked to keep them.

    Most players do not keep either in their collection, so they would only
    show up as "missing". Basic lands are still counted per land, tokens in total.
    """
    keep = {CARD} | ({BASIC} if include_basics else set()) | ({TOKEN} if include_tokens else set())
    tracked, left_out = [], LeftOut()
    for card in cards:
        kind = card_kind(card.name, card.kind)
        if kind in keep:
            tracked.append(card)
        elif kind == BASIC:
            land = BASIC_LANDS.get(name_key(card.name), card.name)
            left_out.basics[land] = left_out.basics.get(land, 0) + card.quantity
        else:
            left_out.tokens += card.quantity
    left_out.basics = {land: left_out.basics[land] for land in BASIC_LAND_NAMES if land in left_out.basics}
    return tracked, left_out


def parse_basic_lands(counts: dict[str, object]) -> dict[str, int]:
    """Validate {"Plains": 10, ...}; unknown names and negative numbers are refused, zeros dropped."""
    basics: dict[str, int] = {}
    for name, value in counts.items():
        land = BASIC_LANDS.get(name_key(str(name)))
        try:
            quantity = int(str(value).strip() or 0)
        except ValueError:
            raise DeckError(f"'{value}' is not a number of {name}") from None
        if land is None:
            raise DeckError(f"{name} is not a basic land")
        if quantity < 0:
            raise DeckError("A deck cannot have a negative number of lands")
        if quantity:
            basics[land] = basics.get(land, 0) + quantity
    return basics


def archidekt_deck_id(source: str) -> int | None:
    match = _ARCHIDEKT_URL.search(source)
    return int(match.group(1)) if match else None


def archidekt_bracket(data: dict) -> int | None:
    bracket = data.get("edhBracket")
    return bracket if bracket in BRACKETS else None


def parse_archidekt_deck(data: dict) -> tuple[str, list[DeckCard]]:
    # Archidekt marks categories such as "Maybeboard" and "Tokens & Extras" as not part of the deck.
    # A card belongs to its first category. (A user category called "Tokens" is a theme, not tokens.)
    outside_deck = {
        str(category.get("name")).casefold()
        for category in data.get("categories") or []
        if category.get("includedInDeck") is False
    }
    cards = []
    for item in data.get("cards") or []:
        oracle = (item.get("card") or {}).get("oracleCard") or {}
        name = oracle.get("name")
        categories = [str(c).casefold() for c in item.get("categories") or []]
        primary = categories[0] if categories else ""
        if not name:
            continue
        if primary in TOKEN_CATEGORIES or oracle.get("layout") in TOKEN_LAYOUTS or "Token" in (oracle.get("types") or []):
            kind = TOKEN
        elif primary in outside_deck or set(categories) & SKIPPED_CATEGORIES:
            continue
        elif "Basic" in (oracle.get("superTypes") or []) and "Land" in (oracle.get("types") or []):
            kind = BASIC
        else:
            kind = CARD
        cards.append(DeckCard(name=name, quantity=int(item.get("quantity") or 1), kind=kind))
    return data.get("name") or "Archidekt deck", cards


async def fetch_archidekt_deck(
    deck_id: int, transport: httpx.AsyncBaseTransport | None = None
) -> tuple[str, list[DeckCard]]:
    return parse_archidekt_deck(await fetch_archidekt_json(deck_id, transport))


async def fetch_archidekt_json(deck_id: int, transport: httpx.AsyncBaseTransport | None = None) -> dict:
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
        data = response.json()
    except ValueError as exc:
        raise DeckError("Archidekt returned an unexpected response") from exc
    if not isinstance(data, dict):
        raise DeckError("Archidekt returned an unexpected response")
    return data


def _names(full_name: str) -> set[str]:
    """A split/double-faced card answers to its full name and to each face."""
    return {name_key(part) for part in (full_name, *full_name.split(" // "))}


@dataclass
class _Need:
    name: str
    quantity: int
    entry_id: int | None = None
    pin: int | None = None
    unassigned: bool = False


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
            cards.setdefault(entry.deck_id, []).append(
                DeckCard(entry.name, entry.quantity, entry.id, unassigned=entry.unassigned)
            )
        self.needs = {deck.id: self._needs(cards[deck.id], pins) for deck in self.decks}

        # claims[deck id][card key][collection entry id] = copies
        self.remaining = {row.entry.id: row.entry.quantity for rows in self.printings.values() for row in rows}
        self.claims: dict[int, dict[str, dict[int, int]]] = {deck.id: {} for deck in self.decks}
        for deck in self.decks:  # chosen printings first
            for key, need in self.needs[deck.id].items():
                if need.pin is not None and not need.unassigned:
                    self._take([need.pin], need.quantity, self.remaining, self.claims[deck.id].setdefault(key, {}))
        for deck in self.decks:  # then whatever is still needed
            for key, need in self.needs[deck.id].items():
                claimed = self.claims[deck.id].setdefault(key, {})
                if need.unassigned:
                    continue
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
            need.unassigned = need.unassigned or card.unassigned
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
                    unassigned=need.unassigned,
                )
            )
        return sorted(rows, key=lambda row: name_key(row.name))

def check_deck(session: Session, cards: list[DeckCard], deck_id: int | None = None) -> list[DeckRow]:
    """Compare a deck with the collection by card name (any printing counts).

    Copies that saved decks already use are not counted again. Pass `deck_id`
    when the cards belong to a saved deck, so it is not competing with itself.
    """
    return _CardPool(session).rows(cards, deck_id)


def save_deck(
    session: Session,
    name: str,
    cards: list[DeckCard],
    left_out: LeftOut | None = None,
    bracket: int | None = None,
) -> Deck:
    """Save a deck. `left_out` records its basic lands and tokens, which are not tracked as cards."""
    deck = Deck(name=name.strip() or "Untitled deck", bracket=bracket if bracket in BRACKETS else None)
    session.add(deck)
    session.flush()
    if left_out is not None:
        set_basic_lands(session, deck, left_out.basics)
        if left_out.tokens > 0:
            session.add(DeckSkipped(deck_id=deck.id, kind=TOKEN, quantity=left_out.tokens))
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
    for model in (DeckSkipped, DeckBasicLand):
        for row in session.exec(select(model).where(model.deck_id == deck.id)).all():
            session.delete(row)
    session.delete(deck)
    session.flush()


def set_basic_lands(session: Session, deck: Deck, counts: dict[str, object]) -> dict[str, int]:
    """Replace the basic lands of a deck with `counts` ({"Plains": 10, ...})."""
    basics = parse_basic_lands(counts)
    for row in session.exec(select(DeckBasicLand).where(DeckBasicLand.deck_id == deck.id)).all():
        session.delete(row)
    session.flush()
    for land, quantity in basics.items():
        session.add(DeckBasicLand(deck_id=deck.id, name=land, quantity=quantity))
    session.flush()
    return basics


async def add_card(
    session: Session,
    scryfall: ScryfallClient,
    deck: Deck,
    name: str,
    set_code: str | None = None,
    collector_number: str | None = None,
    quantity: int = 1,
) -> str:
    """Add a card to a saved deck, looked up like in the collection; returns a message for the user.

    A card that is already in the deck gets more copies; a basic land goes to the
    deck's land counts. When a set is given and that printing is in the collection,
    the deck is set to use that printing.
    """
    if quantity < 1:
        raise DeckError("Add at least one copy")
    data = await find_card(scryfall, name, set_code, collector_number)

    if name_key(data.name) in BASIC_LANDS:
        land = BASIC_LANDS[name_key(data.name)]
        counts = dict(deck_left_out(session, deck).basics)
        counts[land] = counts.get(land, 0) + quantity
        set_basic_lands(session, deck, counts)
        return f"Added {quantity}× {land} to the basic lands of this deck"

    entries = session.exec(select(DeckEntry).where(DeckEntry.deck_id == deck.id)).all()
    entry = next((e for e in entries if _names(e.name) & _names(data.name)), None)
    if entry is None:
        entry = DeckEntry(deck_id=deck.id, name=data.name, quantity=0)
    entry.quantity += quantity
    session.add(entry)
    session.flush()

    if set_code:
        owned = session.exec(
            select(CollectionEntry)
            .join(Card, CollectionEntry.card_id == Card.id)
            .where(Card.scryfall_id == data.scryfall_id)
            .order_by(CollectionEntry.is_foil)
        ).first()
        if owned is not None:
            choose_printing(session, deck, entry.id, owned.id)
    total = f" (now {entry.quantity} in this deck)" if entry.quantity != quantity else ""
    return f"Added {quantity}× {data.name} to this deck{total}"


def set_assigned(session: Session, deck: Deck, entry_id: int, assigned: bool) -> DeckEntry:
    """Take your copies of a card out of a deck (it then counts as missing), or put them back."""
    entry = session.get(DeckEntry, entry_id)
    if entry is None or entry.deck_id != deck.id:
        raise DeckError("That card is not in this deck")
    entry.unassigned = not assigned
    session.add(entry)
    session.flush()
    return entry


def update_deck(
    session: Session,
    deck: Deck,
    bracket: int | None,
    basic_lands: dict[str, object],
    printings: dict[int, int | None],
) -> None:
    """Save everything that can be edited on a deck in one go.

    `printings` maps a deck entry id to the chosen collection entry id (None = let the app choose).
    Nothing is stored when any part is invalid.
    """
    basics = parse_basic_lands(basic_lands)
    set_bracket(session, deck, bracket)
    set_basic_lands(session, deck, basics)
    for entry_id, collection_entry_id in printings.items():
        choose_printing(session, deck, entry_id, collection_entry_id)


def random_decks(
    summaries: list[DeckSummary],
    count: int = 1,
    bracket: int | None = None,
    complete_only: bool = False,
    rng: random.Random | None = None,
) -> list[DeckSummary]:
    """Pick `count` different decks at random, e.g. one per player at the table.

    Raises DeckError when fewer decks match than were asked for.
    """
    eligible = [
        summary
        for summary in summaries
        if (bracket is None or summary.deck.bracket == bracket) and (summary.complete or not complete_only)
    ]
    if count < 1:
        raise DeckError("Pick at least one deck")
    if len(eligible) < count:
        found = "no decks" if not eligible else f"only {len(eligible)} deck{'s' if len(eligible) != 1 else ''}"
        raise DeckError(f"You asked for {count}, but {found} match")
    return (rng or random).sample(eligible, count)


def tracked_basic_lands(session: Session, deck: Deck) -> int:
    """Basic lands that are still in the deck's card list (decks saved before lands were counted apart)."""
    entries = session.exec(select(DeckEntry).where(DeckEntry.deck_id == deck.id))
    return sum(entry.quantity for entry in entries if name_key(entry.name) in BASIC_LANDS)


def move_basic_lands_out(session: Session, deck: Deck) -> int:
    """Take the basic lands out of a deck's card list and add them to its per-land counts.

    Returns the number of copies moved. Their copies in the collection are free again.
    """
    counts = dict(deck_left_out(session, deck).basics)
    moved = 0
    for entry in session.exec(select(DeckEntry).where(DeckEntry.deck_id == deck.id)).all():
        land = BASIC_LANDS.get(name_key(entry.name))
        if land is None:
            continue
        counts[land] = counts.get(land, 0) + entry.quantity
        moved += entry.quantity
        for pick in session.exec(select(DeckPick).where(DeckPick.deck_entry_id == entry.id)).all():
            session.delete(pick)
        session.delete(entry)
    session.flush()
    set_basic_lands(session, deck, counts)
    return moved


def set_bracket(session: Session, deck: Deck, bracket: int | None) -> None:
    if bracket is not None and bracket not in BRACKETS:
        raise DeckError("A bracket is a number from 1 to 5")
    deck.bracket = bracket
    session.add(deck)
    session.flush()


def deck_left_out(session: Session, deck: Deck) -> LeftOut:
    """The basic lands (per land) and tokens of a saved deck."""
    lands = {row.name: row.quantity for row in session.exec(select(DeckBasicLand).where(DeckBasicLand.deck_id == deck.id))}
    tokens = sum(
        row.quantity
        for row in session.exec(select(DeckSkipped).where(DeckSkipped.deck_id == deck.id, DeckSkipped.kind == TOKEN))
    )
    return LeftOut(basics={name: lands[name] for name in BASIC_LAND_NAMES if lands.get(name)}, tokens=tokens)


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
    basics: dict[int, int] = {}
    for row in session.exec(select(DeckBasicLand)):
        basics[row.deck_id] = basics.get(row.deck_id, 0) + row.quantity
    summaries = []
    for deck in pool.decks:
        rows = pool.rows(deck_id=deck.id)
        summaries.append(
            DeckSummary(
                deck,
                needed=sum(row.needed for row in rows),
                usable=sum(row.usable for row in rows),
                value=round(sum(row.value for row in rows), 2),
                basic_lands=basics.get(deck.id, 0),
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
    await scryfall.fill_missing_prices([data for data in resolved if data is not None])
    for name, data in zip(names, resolved):
        if data is None:
            not_found.append(name)
            continue
        card = store_scryfall_card(session, data)
        target = round((data.price_eur or 0.0) * target_pct / 100, 2)
        add_to_wishlist(session, card, target, data.cardmarket_url)
        added += 1
    return added, not_found
