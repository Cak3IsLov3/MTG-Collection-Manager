"""Decks: which cards of a deck do you own, which are tied up in other decks, and which are missing."""

import asyncio
import itertools
import logging
import random
import re
from dataclasses import dataclass, field

import httpx
from sqlmodel import Session, select

from app.config import get_settings
from app.csv_import import CsvFormatError, parse_collection_csv
from app.models import (
    utcnow,
    Card,
    CollectionEntry,
    Deck,
    DeckBasicLand,
    DeckEntry,
    DeckFolder,
    DeckPick,
    DeckSkipped,
    DeckToken,
    PriceSnapshot,
    WishlistEntry,
)
from app.scryfall import CardRef, ScryfallClient, name_key
from app.services.collection import CollectionRow, _fold, add_to_collection, collection_rows, find_card
from app.services.games import forget_deck
from app.services.pricing import SCRYFALL, price_for, store_scryfall_card
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
_LINE = re.compile(r"^(?P<qty>\d+)\s*x?\s+(?P<name>.+?)(?:\s+\((?P<set>[A-Za-z0-9]{2,6})\)(?:\s+(?P<num>\S+))?)?$")


logger = logging.getLogger(__name__)

ARCHIDEKT_DECK_URL = "https://archidekt.com/decks/{deck_id}"


class DeckError(Exception):
    pass


@dataclass
class DeckCard:
    name: str
    quantity: int = 1
    entry_id: int | None = None  # set for the cards of a saved deck
    kind: str = "card"  # CARD, BASIC or TOKEN, when the source says so
    unassigned: bool = False  # the user took their copies out of the deck
    # The printing the deck list asks for, when it names one (Archidekt does, plain names do not).
    set_code: str | None = None
    collector_number: str | None = None
    scryfall_id: str | None = None
    foil: bool | None = None
    commander: bool = False  # the deck list puts it in the Commander category

    def wants(self, row: CollectionRow) -> bool:
        """Is this collection entry the printing the deck list asks for?"""
        return _same_printing(self, row)


@dataclass
class LeftOut:
    """What a deck contains besides the cards that are tracked against the collection."""

    basics: dict[str, int] = field(default_factory=dict)  # "Plains" -> copies
    tokens: int = 0
    token_cards: list[DeckCard] = field(default_factory=list)  # which tokens, when the list names them

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
    # New copies from an import that wait for you to put them in a deck.
    waiting: int = 0
    # The printing the deck list asks for, when it names one.
    set_code: str | None = None
    collector_number: str | None = None
    scryfall_id: str | None = None
    foil: bool | None = None

    @property
    def wanted_printing(self) -> str | None:
        """'CMM #410 foil', or None when the deck list does not name a printing."""
        if not self.set_code:
            return None
        number = f" #{self.collector_number}" if self.collector_number else ""
        return f"{self.set_code.upper()}{number}{' foil' if self.foil else ''}"

    @property
    def uses_wanted_printing(self) -> bool:
        return any(_same_printing(self, row) for row, _ in self.picks)

    def card_ref(self) -> CardRef:
        """For looking the card up on Scryfall, in the printing the deck asks for if it names one."""
        return CardRef(self.name, self.set_code, self.collector_number, self.scryfall_id)

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
    def can_add(self) -> bool:
        """A card of a saved deck that "Add all cards to this deck" can do something for: you took your copies
        out of it, or new copies from an import wait for it."""
        return bool(self.entry_id) and self.missing > 0 and (self.waiting > 0 or self.unassigned)

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
    tokens: int = 0  # tokens in its deck list (not counted as cards)

    @property
    def uses_tokens(self) -> bool:
        """As you set it on the deck, or else: its deck list has tokens."""
        return self.deck.uses_tokens if self.deck.uses_tokens is not None else self.tokens > 0

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


def _same_printing(wanted, row: CollectionRow) -> bool:
    """`wanted` is anything with scryfall_id / set_code / collector_number (a deck card, need or row)."""
    if wanted.scryfall_id:
        return row.card.scryfall_id == wanted.scryfall_id
    if not wanted.set_code or row.card.set_code != wanted.set_code.lower():
        return False
    return not wanted.collector_number or row.card.collector_number == wanted.collector_number


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
        cards.append(
            DeckCard(
                name=match["name"].strip(),
                quantity=int(match["qty"]),
                kind=kind,
                commander=category == "commander",
                set_code=match["set"].lower() if match["set"] else None,
                collector_number=match["num"] if match["set"] else None,
                foil=True if "*F*" in line else None,
            )
        )
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
            left_out.token_cards.append(card)
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
        elif primary in outside_deck or primary in SKIPPED_CATEGORIES:  # a second category "Maybeboard" is only a label
            continue
        elif "Basic" in (oracle.get("superTypes") or []) and "Land" in (oracle.get("types") or []):
            kind = BASIC
        else:
            kind = CARD
        printing = item.get("card") or {}
        cards.append(
            DeckCard(
                name=name,
                quantity=int(item.get("quantity") or 1),
                kind=kind,
                # Archidekt knows the exact printing of every card in the deck.
                set_code=((printing.get("edition") or {}).get("editioncode") or "").lower() or None,
                collector_number=str(printing.get("collectorNumber") or "") or None,
                scryfall_id=printing.get("uid") or None,
                foil={"foil": True, "etched": True, "normal": False}.get(str(item.get("modifier") or "").casefold()),
                commander=primary == "commander",
            )
        )
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
    set_code: str | None = None
    collector_number: str | None = None
    scryfall_id: str | None = None
    foil: bool | None = None


class _CardPool:
    """The collection, shared out over the saved decks.

    Every physical copy can only be in one deck. Printings a user chose for a
    deck are handed out first. Then every deck gets the printing its deck list
    names, if you own it: that copy belongs in that deck, even when an older
    deck could have used it too. After that saved decks claim what they still
    need in the order they were created, cheapest printing first. Whatever is
    left is available to a deck you are only checking.

    New copies from an import are held back from the automatic steps: they wait
    until you put them in a deck yourself (which makes them a chosen printing)
    or let the app decide.
    """

    def __init__(self, session: Session, rows: list[CollectionRow] | None = None) -> None:
        self.printings: dict[str, list[CollectionRow]] = {}
        self._alias: dict[str, str] = {}  # "fire" -> "fire // ice"
        for row in collection_rows(session) if rows is None else rows:
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
                DeckCard(
                    entry.name, entry.quantity, entry.id, unassigned=entry.unassigned, set_code=entry.set_code,
                    collector_number=entry.collector_number, scryfall_id=entry.scryfall_id, foil=entry.foil,
                )
            )
        self.needs = {deck.id: self._needs(cards[deck.id], pins) for deck in self.decks}

        # claims[deck id][card key][collection entry id] = copies
        self.remaining = {row.entry.id: row.entry.quantity for rows in self.printings.values() for row in rows}
        self.claims: dict[int, dict[str, dict[int, int]]] = {deck.id: {} for deck in self.decks}
        # A deck you are only testing takes nothing from the collection: its cards all stay missing.
        claiming = [deck for deck in self.decks if not deck.testing]
        for deck in claiming:  # chosen printings first
            for key, need in self.needs[deck.id].items():
                if need.pin is not None and not need.unassigned:
                    self._take([need.pin], need.quantity, self.remaining, self.claims[deck.id].setdefault(key, {}))
        # New copies from an import wait for you: the automatic steps below do not get them.
        self.waiting: dict[int, int] = {}
        for rows in self.printings.values():
            for row in rows:
                held = min(row.entry.unsorted or 0, self.remaining[row.entry.id])
                if held > 0:
                    self.waiting[row.entry.id] = held
                    self.remaining[row.entry.id] -= held
        for deck in claiming:  # then the printing a deck list names
            for key, need in self.needs[deck.id].items():
                if need.unassigned or not need.set_code:
                    continue
                claimed = self.claims[deck.id].setdefault(key, {})
                self._take(self._wanted_ids(key, need), need.quantity - sum(claimed.values()), self.remaining, claimed)
        for deck in claiming:  # then whatever is still needed
            for key, need in self.needs[deck.id].items():
                claimed = self.claims[deck.id].setdefault(key, {})
                if need.unassigned:
                    continue
                self._take(self._order(key, need), need.quantity - sum(claimed.values()), self.remaining, claimed)
        for entry_id, held in self.waiting.items():  # free, but still waiting to be sorted
            self.remaining[entry_id] += held

    @staticmethod
    def _rank(need: _Need, row: CollectionRow) -> int:
        """0: the printing the deck list names, in the finish it names; 1: that printing in the
        other finish; 2: any other printing."""
        if not _same_printing(need, row):
            return 2
        return 0 if need.foil is None or row.entry.is_foil == need.foil else 1

    def _order(self, key: str, need: _Need) -> list[int]:
        """Collection entries to take from: the printing the deck list asks for first, then the
        rest from cheap to expensive."""
        rows = self.printings.get(key, [])
        return [row.entry.id for row in sorted(rows, key=lambda row: self._rank(need, row))]  # stable sort

    def _wanted_ids(self, key: str, need: _Need) -> list[int]:
        """Only the collection entries that are the printing the deck list names."""
        rows = sorted(self.printings.get(key, []), key=lambda row: self._rank(need, row))
        return [row.entry.id for row in rows if self._rank(need, row) < 2]

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
            if need.set_code is None and card.set_code:
                need.set_code, need.collector_number = card.set_code, card.collector_number
                need.scryfall_id, need.foil = card.scryfall_id, card.foil
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
                self._take(self._order(key, need), need.quantity, leftover, mine)
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
                    waiting=sum(self.waiting.get(row.entry.id, 0) for row in printings),
                    set_code=need.set_code,
                    collector_number=need.collector_number,
                    scryfall_id=need.scryfall_id,
                    foil=need.foil,
                )
            )
        return sorted(rows, key=lambda row: name_key(row.name))

@dataclass
class CollectionDecks:
    """How the collection is shared out over the decks, per collection entry id."""

    usage: dict[int, list[tuple[Deck, int]]]  # decks that use its copies: [(deck, copies)]
    wanted: dict[int, list[tuple[Deck, int, int]]]  # decks that miss the card: [(deck, copies missing, deck entry id)]
    waiting: dict[int, int]  # new copies that wait for you to sort them into a deck


def collection_decks(session: Session, rows: list[CollectionRow] | None = None) -> CollectionDecks:
    """`rows`: the collection rows when you have them already (they are sorted again, in place)."""
    pool = _CardPool(session, list(rows) if rows is not None else None)
    return CollectionDecks(_usage(pool), _wanted(pool), dict(pool.waiting))


def deck_usage(session: Session) -> dict[int, list[tuple[Deck, int]]]:
    """Which saved decks use the copies of each collection entry: {collection entry id: [(deck, copies)]}."""
    return _usage(_CardPool(session))


def _usage(pool: "_CardPool") -> dict[int, list[tuple[Deck, int]]]:
    usage: dict[int, list[tuple[Deck, int]]] = {}
    for deck in pool.decks:
        per_entry: dict[int, int] = {}
        for claimed in pool.claims[deck.id].values():
            for entry_id, copies in claimed.items():
                per_entry[entry_id] = per_entry.get(entry_id, 0) + copies
        for entry_id, copies in per_entry.items():
            if copies:
                usage.setdefault(entry_id, []).append((deck, copies))
    return usage


def deck_wanted(session: Session) -> dict[int, list[tuple[Deck, int, int]]]:
    """Free copies a saved deck could still use: {collection entry id: [(deck, copies missing, deck entry id)]}.

    A deck misses a card you own a free copy of when you took the card out of that deck,
    or when the copy is new from an import and waits for you to sort it."""
    return _wanted(_CardPool(session))


def _wanted(pool: "_CardPool") -> dict[int, list[tuple[Deck, int, int]]]:
    wanted: dict[int, list[tuple[Deck, int, int]]] = {}
    for deck in pool.decks:
        for key, need in pool.needs[deck.id].items():
            missing = need.quantity - sum(pool.claims[deck.id].get(key, {}).values())
            if missing <= 0:
                continue
            for row in pool.printings.get(key, []):
                if pool.remaining[row.entry.id] > 0:
                    wanted.setdefault(row.entry.id, []).append((deck, missing, need.entry_id))
    return wanted


def put_new_copy_in_deck(
    session: Session, deck: Deck, entry_id: int, collection_entry_id: int | None = None
) -> str:
    """Put new copies from an import in a deck that misses the card; returns a message for the user.

    Without `collection_entry_id` the printing the deck list names goes first, then the
    cheapest. The copies become the deck's chosen printing, so no other deck takes them.
    """
    entry = session.get(DeckEntry, entry_id)
    if entry is None or entry.deck_id != deck.id:
        raise DeckError("That card is not in this deck")
    pool = _CardPool(session)
    key = pool._key(entry.name)
    need = pool.needs[deck.id][key]
    claimed = sum(pool.claims[deck.id].get(key, {}).values())
    missing = need.quantity if need.unassigned else need.quantity - claimed
    if missing <= 0:
        raise DeckError(f"{deck.name} is not missing {entry.name}")
    candidates = [owned_id for owned_id in pool._order(key, need) if pool.waiting.get(owned_id)]
    if collection_entry_id is not None:
        candidates = [owned_id for owned_id in candidates if owned_id == collection_entry_id]
    if not candidates:
        raise DeckError(f"You have no new copy of {entry.name} waiting to be sorted")
    chosen = candidates[0]
    choose_printing(session, deck, entry.id, chosen)
    if entry.unassigned:
        entry.unassigned = False
        session.add(entry)
    owned = session.get(CollectionEntry, chosen)
    copies = min(missing, pool.waiting[chosen])
    owned.unsorted = max(0, owned.unsorted - copies)
    session.add(owned)
    session.flush()
    return f"Put {copies}× {entry.name} in {deck.name}"


@dataclass
class AddAllResult:
    new_copies: int = 0  # cards that got new copies from an import that waited for them
    put_back: int = 0  # cards you had taken out of the deck
    added: int = 0  # copies put in your collection
    not_found: list[str] = field(default_factory=list)  # cards Scryfall could not find

    @property
    def nothing(self) -> bool:
        return not (self.new_copies or self.put_back or self.added)


async def add_all_missing(session: Session, scryfall: ScryfallClient, deck: Deck) -> AddAllResult:
    """Make the deck complete: cards you took out come back, new copies from an import that wait go in, and
    for what is still missing one copy per missing card is added to your collection (in the printing the deck
    list names, else by name), so this deck gets them."""
    result = AddAllResult()
    for row in saved_deck_rows(session, deck):
        if not row.can_add:
            continue
        if row.waiting:
            try:
                put_new_copy_in_deck(session, deck, row.entry_id)
                result.new_copies += 1
                continue
            except DeckError:
                pass  # no new copy of this card after all
        if row.unassigned:
            set_assigned(session, deck, row.entry_id, True)
            result.put_back += 1
    rows = [row for row in saved_deck_rows(session, deck) if row.entry_id and row.missing > 0]
    if rows:
        resolved = await scryfall.resolve([row.card_ref() for row in rows])
        found = [(row, data) for row, data in zip(rows, resolved) if data is not None]
        result.not_found = [row.name for row, data in zip(rows, resolved) if data is None]
        await scryfall.fill_missing_prices(
            [data for _, data in found], {data.scryfall_id for row, data in found if row.foil}
        )
        for row, data in found:
            add_to_collection(session, store_scryfall_card(session, data), row.missing, bool(row.foil))
            result.added += row.missing
    return result


def let_app_sort(session: Session, collection_entry_id: int | None = None) -> int:
    """Stop holding new copies back (of one collection entry, or all): the app shares them out
    over the decks again. Returns the number of copies that were waiting."""
    statement = select(CollectionEntry).where(CollectionEntry.unsorted > 0)
    if collection_entry_id is not None:
        statement = statement.where(CollectionEntry.id == collection_entry_id)
    released = 0
    for owned in session.exec(statement).all():
        released += owned.unsorted
        owned.unsorted = 0
        session.add(owned)
    session.flush()
    return released


def check_deck(session: Session, cards: list[DeckCard], deck_id: int | None = None) -> list[DeckRow]:
    """Compare a deck with the collection by card name (any printing counts).

    Copies that saved decks already use are not counted again. Pass `deck_id`
    when the cards belong to a saved deck, so it is not competing with itself.
    """
    return _CardPool(session).rows(cards, deck_id)


def _one_line_per_card(cards: list[DeckCard]) -> dict[str, DeckCard]:
    """Merge the lines of a deck list by card name. The same card in several printings becomes
    one line; the first printing is kept."""
    merged: dict[str, DeckCard] = {}
    for card in cards:
        key = name_key(card.name)
        if key in merged:
            merged[key].quantity += card.quantity
            merged[key].commander = merged[key].commander or card.commander
        else:
            merged[key] = DeckCard(
                card.name.strip(), card.quantity, set_code=card.set_code, collector_number=card.collector_number,
                scryfall_id=card.scryfall_id, foil=card.foil, commander=card.commander,
            )
    return merged


def token_name(name: str) -> str:
    """"Treasure Token" -> "Treasure": the name a token is shown by."""
    name = name.strip()
    return name[: -len(" token")].strip() if name.casefold().endswith(" token") else name


def _merged_tokens(cards: list[DeckCard]) -> dict[str, DeckCard]:
    merged: dict[str, DeckCard] = {}
    for card in cards:
        name = token_name(card.name)
        if name in merged:
            merged[name].quantity += card.quantity
        else:
            merged[name] = DeckCard(name, card.quantity, scryfall_id=card.scryfall_id, kind=TOKEN)
    return merged


def _set_tokens(session: Session, deck: Deck, tokens: int, token_cards: list[DeckCard] | None = None) -> None:
    """Store how many tokens a deck has and, when the list names them, which ones."""
    for row in session.exec(select(DeckSkipped).where(DeckSkipped.deck_id == deck.id, DeckSkipped.kind == TOKEN)).all():
        session.delete(row)
    if token_cards is not None:
        for row in session.exec(select(DeckToken).where(DeckToken.deck_id == deck.id)).all():
            session.delete(row)
    session.flush()
    if tokens > 0:
        session.add(DeckSkipped(deck_id=deck.id, kind=TOKEN, quantity=tokens))
    for card in _merged_tokens(token_cards or []).values():
        session.add(DeckToken(deck_id=deck.id, name=card.name, quantity=card.quantity, scryfall_id=card.scryfall_id))
    session.flush()


def deck_tokens(session: Session, deck: Deck) -> list[DeckToken]:
    """The tokens of a deck in alphabetical order, whatever the capitals ("beast" before "Food")."""
    tokens = session.exec(select(DeckToken).where(DeckToken.deck_id == deck.id)).all()
    return sorted(tokens, key=lambda token: (name_key(token.name), token.id))


def _count_named_tokens(session: Session, deck: Deck) -> None:
    """After editing the token list by hand, the token count follows the list."""
    _set_tokens(session, deck, sum(token.quantity for token in deck_tokens(session, deck)))


def add_token(session: Session, deck: Deck, name: str, quantity: int = 1) -> DeckToken:
    # "cat beast" -> "Cat beast" until Scryfall gives its exact name ("Cat Beast").
    name = token_name(name)
    name = name[:1].upper() + name[1:]
    if not name:
        raise DeckError("Fill in the name of the token, e.g. Treasure")
    if quantity < 1:
        raise DeckError("Add at least one token")
    token = next(
        (t for t in deck_tokens(session, deck) if name_key(t.name) == name_key(name) and not t.scryfall_id), None
    )
    if token is None:
        token = DeckToken(deck_id=deck.id, name=name, quantity=0)
    token.quantity += quantity
    session.add(token)
    session.flush()
    _count_named_tokens(session, deck)  # "Tokens owned" stays as you set it: listing a token is not owning it
    return token


def set_token_quantity(session: Session, deck: Deck, token_id: int, quantity: int) -> DeckToken:
    """Change how many of a token a deck needs (at least one; remove it to take it off the list)."""
    token = session.get(DeckToken, token_id)
    if token is None or token.deck_id != deck.id:
        raise DeckError("That token is not in this deck")
    if quantity < 1:
        raise DeckError("A token needs a quantity of at least 1; use remove to take it off the list")
    token.quantity = quantity
    session.add(token)
    session.flush()
    _count_named_tokens(session, deck)
    return token


def remove_token(session: Session, deck: Deck, token_id: int) -> None:
    token = session.get(DeckToken, token_id)
    if token is None or token.deck_id != deck.id:
        raise DeckError("That token is not in this deck")
    session.delete(token)
    session.flush()
    _count_named_tokens(session, deck)


def save_deck(
    session: Session,
    name: str,
    cards: list[DeckCard],
    left_out: LeftOut | None = None,
    bracket: int | None = None,
    archidekt_id: int | None = None,
    testing: bool = False,
) -> Deck:
    """Save a deck. `left_out` records its basic lands and tokens, which are not tracked as cards.

    With `archidekt_id` the deck keeps following that Archidekt deck (see sync_deck).
    A `testing` deck takes no copies from the collection.
    """
    deck = Deck(
        testing=testing,
        name=name.strip() or "Untitled deck",
        bracket=bracket if bracket in BRACKETS else None,
        archidekt_id=archidekt_id,
        synced_at=utcnow() if archidekt_id else None,
    )
    session.add(deck)
    session.flush()
    if left_out is not None:
        set_basic_lands(session, deck, left_out.basics)
        _set_tokens(session, deck, left_out.tokens, left_out.token_cards)
    merged = _one_line_per_card(cards)
    for card in merged.values():
        session.add(
            DeckEntry(
                deck_id=deck.id, name=card.name, quantity=card.quantity, set_code=card.set_code,
                collector_number=card.collector_number, scryfall_id=card.scryfall_id, foil=card.foil,
            )
        )
    deck.commander = next((card.name for card in merged.values() if card.commander), None)
    session.add(deck)
    session.flush()
    return deck


@dataclass
class SyncResult:
    """What changed in a deck when it was brought up to date with Archidekt."""

    deck: Deck
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)  # quantity or printing
    other: list[str] = field(default_factory=list)  # name, bracket, basic lands, tokens
    error: str | None = None

    @property
    def anything(self) -> bool:
        return bool(self.added or self.removed or self.changed or self.other)

    def summary(self) -> str:
        if self.error:
            return f"{self.deck.name}: could not sync ({self.error})"
        if not self.anything:
            return f"{self.deck.name} is already up to date with Archidekt"
        parts = []
        for label, names in (("added", self.added), ("removed", self.removed), ("changed", self.changed)):
            if names:
                shown = ", ".join(names[:5]) + (f" and {len(names) - 5} more" if len(names) > 5 else "")
                parts.append(f"{label} {shown}")
        parts += self.other
        return f"{self.deck.name} updated from Archidekt: " + "; ".join(parts)


def deck_url(deck: Deck) -> str | None:
    return ARCHIDEKT_DECK_URL.format(deck_id=deck.archidekt_id) if deck.archidekt_id else None


def apply_archidekt(session: Session, deck: Deck, data: dict) -> SyncResult:
    """Make a saved deck match its Archidekt deck (the raw API response `data`).

    Cards that stay keep their line, so a printing you chose for them and cards
    you took out of the deck are not lost. Basic lands, tokens, bracket and name
    are taken over from Archidekt.
    """
    result = SyncResult(deck)
    name, cards = parse_archidekt_deck(data)
    # A deck that tracks its basic lands as cards keeps doing so.
    tracked, left_out = select_tracked(cards, include_basics=tracked_basic_lands(session, deck) > 0)
    wanted = _one_line_per_card(tracked)
    if not wanted:
        raise DeckError("The Archidekt deck has no cards")

    entries = session.exec(select(DeckEntry).where(DeckEntry.deck_id == deck.id).order_by(DeckEntry.id)).all()
    kept: set[int] = set()
    for card in wanted.values():
        entry = next((e for e in entries if e.id not in kept and _names(e.name) & _names(card.name)), None)
        if entry is None:
            session.add(
                DeckEntry(
                    deck_id=deck.id, name=card.name, quantity=card.quantity, set_code=card.set_code,
                    collector_number=card.collector_number, scryfall_id=card.scryfall_id, foil=card.foil,
                )
            )
            result.added.append(card.name)
            continue
        kept.add(entry.id)
        before = (entry.quantity, entry.set_code, entry.collector_number, entry.scryfall_id, entry.foil)
        after = (card.quantity, card.set_code, card.collector_number, card.scryfall_id, card.foil)
        if before != after:
            entry.quantity, entry.set_code, entry.collector_number, entry.scryfall_id, entry.foil = after
            session.add(entry)
            result.changed.append(card.name)
    for entry in entries:
        if entry.id in kept:
            continue
        for pick in session.exec(select(DeckPick).where(DeckPick.deck_entry_id == entry.id)).all():
            session.delete(pick)
        session.delete(entry)
        result.removed.append(entry.name)
    session.flush()

    current = deck_left_out(session, deck)
    if current.basics != left_out.basics:
        set_basic_lands(session, deck, left_out.basics)
        result.other.append(f"basic lands now {left_out.basic_total}")
    named = {token.name: token.quantity for token in deck_tokens(session, deck)}
    wanted_tokens = {name: card.quantity for name, card in _merged_tokens(left_out.token_cards).items()}
    if current.tokens != left_out.tokens or named != wanted_tokens:
        _set_tokens(session, deck, left_out.tokens, left_out.token_cards)
        result.other.append(f"tokens now {left_out.tokens}")
    bracket = archidekt_bracket(data)
    if bracket and bracket != deck.bracket:
        deck.bracket = bracket
        result.other.append(f"bracket now {bracket}")
    if data.get("name") and name != deck.name:
        result.other.append(f"renamed from {deck.name}")
        deck.name = name
    commander = next((card.name for card in wanted.values() if card.commander), None)
    if commander and commander != deck.commander:
        if deck.commander:
            result.other.append(f"commander now {commander}")
        deck.commander, deck.commander_image = commander, None
    deck.synced_at = utcnow()
    if result.added or result.removed:
        deck.colors = None  # worked out again by fill_deck_colors
    session.add(deck)
    session.flush()
    return result


async def sync_deck(session: Session, deck: Deck) -> SyncResult:
    """Bring a deck up to date with the Archidekt deck it follows."""
    if not deck.archidekt_id:
        raise DeckError("This deck has no Archidekt link")
    return apply_archidekt(session, deck, await fetch_archidekt_json(deck.archidekt_id))


async def sync_all_decks(session: Session, pause: float = 1.0) -> list[SyncResult]:
    """Sync every deck that follows an Archidekt link; one failing deck does not stop the rest."""
    results = []
    linked = session.exec(select(Deck).where(Deck.archidekt_id != None).order_by(Deck.id)).all()  # noqa: E711
    for index, deck in enumerate(linked):
        if index and pause:
            await asyncio.sleep(pause)  # be polite to Archidekt
        try:
            results.append(await sync_deck(session, deck))
        except DeckError as exc:
            logger.warning("Could not sync deck %s with Archidekt: %s", deck.name, exc)
            results.append(SyncResult(deck, error=str(exc)))
    return results


def deck_card_names(session: Session) -> dict[int, list[str]]:
    """The card names of every saved deck, basic lands included: {deck id: [names]}."""
    names: dict[int, list[str]] = {}
    for entry in session.exec(select(DeckEntry).order_by(DeckEntry.name)).all():
        names.setdefault(entry.deck_id, []).append(entry.name)
    for land in session.exec(select(DeckBasicLand).order_by(DeckBasicLand.name)).all():
        names.setdefault(land.deck_id, []).append(land.name)
    return names


def search_decks(summaries: list[DeckSummary], card_names: dict[int, list[str]], query: str | None) -> list[DeckSummary]:
    """Decks whose name, or one of whose cards, contains every word of `query` (accents do not matter)."""
    words = _fold(query or "").split()
    if not words:
        return summaries
    return [
        summary
        for summary in summaries
        if all(word in _fold(summary.deck.name) for word in words)
        or any(all(word in _fold(name) for word in words) for name in card_names.get(summary.deck.id, []))
    ]


async def fill_deck_colors(session: Session, scryfall: ScryfallClient) -> int:
    """Work out the colour identity of decks that have none yet: the colours of all their cards.

    Colours of cards in the collection are known already; the rest are looked up on Scryfall.
    Returns the number of decks that got their colours."""
    pending = session.exec(select(Deck).where(Deck.colors == None)).all()  # noqa: E711
    if not pending:
        return 0
    entries = session.exec(select(DeckEntry).where(DeckEntry.deck_id.in_([deck.id for deck in pending]))).all()
    known: dict[str, str] = {}
    for card in session.exec(select(Card).where(Card.identity != None)).all():  # noqa: E711
        for key in _names(card.name):
            known.setdefault(key, card.identity)
    lookups: dict[str, dict] = {}
    for entry in entries:
        if not _names(entry.name) & known.keys():
            lookups.setdefault(name_key(entry.name), {"id": entry.scryfall_id} if entry.scryfall_id else {"name": entry.name})
    if lookups:
        found, _ = await scryfall.get_collection(list(lookups.values()))
        for data in found:
            if data.identity is not None:
                for key in _names(data.name):
                    known.setdefault(key, data.identity)
    for deck in pending:
        colors = set()
        for entry in entries:
            if entry.deck_id == deck.id:
                colors.update(next((known[key] for key in _names(entry.name) if key in known), ""))
        deck.colors = "".join(c for c in "WUBRG" if c in colors)
        session.add(deck)
    session.commit()
    return len(pending)


def set_archidekt_link(session: Session, deck: Deck, url: str | None) -> bool:
    """Set or clear the Archidekt deck a saved deck follows; returns True when the link changed."""
    archidekt_id = None
    if url and url.strip():
        archidekt_id = archidekt_deck_id(url)
        if archidekt_id is None:
            raise DeckError("That does not look like an Archidekt deck URL")
    if archidekt_id == deck.archidekt_id:
        return False
    deck.archidekt_id = archidekt_id
    if archidekt_id is None:
        deck.synced_at = None
    session.add(deck)
    session.flush()
    return True


def delete_deck(session: Session, deck: Deck) -> None:
    """Remove a saved deck; its cards become available to other decks again."""
    for entry in session.exec(select(DeckEntry).where(DeckEntry.deck_id == deck.id)).all():
        for pick in session.exec(select(DeckPick).where(DeckPick.deck_entry_id == entry.id)).all():
            session.delete(pick)
        session.delete(entry)
    for model in (DeckSkipped, DeckBasicLand, DeckToken):
        for row in session.exec(select(model).where(model.deck_id == deck.id)).all():
            session.delete(row)
    forget_deck(session, deck.id)  # its games stay in the log
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
        deck.colors = None  # worked out again by fill_deck_colors
        session.add(deck)
    entry.quantity += quantity
    if set_code:  # the user named a printing: that is what this deck asks for from now on
        entry.set_code, entry.collector_number = data.set_code, data.collector_number
        entry.scryfall_id, entry.foil = data.scryfall_id, None
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
    skip: set[int] | frozenset[int] = frozenset(),
) -> list[DeckSummary]:
    """Pick `count` different decks at random, e.g. one per player at the table; never one in `skip`.

    Raises DeckError when fewer decks match than were asked for.
    """
    eligible = [
        summary
        for summary in summaries
        if (bracket is None or summary.deck.bracket == bracket) and (summary.complete or not complete_only)
        and summary.deck.id not in skip
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


def set_commander(session: Session, deck: Deck, name: str | None) -> None:
    """Choose the commander of a deck: a card in it, or None/"" for no commander."""
    name = (name or "").strip()
    if name:
        entries = session.exec(select(DeckEntry).where(DeckEntry.deck_id == deck.id)).all()
        entry = next((e for e in entries if _names(e.name) & _names(name)), None)
        if entry is None:
            raise DeckError(f"{name} is not in this deck")
        name = entry.name
    if name != deck.commander:
        deck.commander, deck.commander_image = name, None
        session.add(deck)
        session.flush()


def _guess_commander(deck: Deck, entries: list[DeckEntry]) -> str | None:
    """Many decks are named after their commander ("Optimus Prime, Hero", "Rydia, Summoner of
    Mist - Lands Matter"): the card whose name the deck name starts with."""
    deck_name = name_key(deck.name)
    names = [(len(key), entry.name) for entry in entries for key in _names(entry.name) if deck_name.startswith(key)]
    return max(names)[1] if names else None


def _can_command(type_line: str) -> bool:
    return "Legendary" in type_line and ("Creature" in type_line or "Planeswalker" in type_line)


def deck_list_commander_image(session: Session, deck: Deck) -> str | None:
    """The picture of the commander in the printing the deck list names, if it names one."""
    for entry in session.exec(select(DeckEntry).where(DeckEntry.deck_id == deck.id)).all():
        if not _names(entry.name) & _names(deck.commander or ""):
            continue
        if entry.scryfall_id:
            card = session.exec(select(Card).where(Card.scryfall_id == entry.scryfall_id)).first()
            if card is not None and card.image_url:
                return card.image_url
        return entry.image_url or None
    return None


def update_commander_image(session: Session, deck: Deck, rows: list[DeckRow]) -> bool:
    """Show the commander in the printing this deck uses (the copy set aside for it), else the one
    the deck list names. Returns whether the picture changed."""
    if not deck.commander:
        return False
    row = next((r for r in rows if _names(r.name) & _names(deck.commander)), None)
    image = None
    if row is not None and row.picks:
        image = row.picks[0][0].card.image_url
    image = image or deck_list_commander_image(session, deck)
    if not image or image == deck.commander_image:
        return False
    deck.commander_image = image
    session.add(deck)
    session.flush()
    return True


async def fill_deck_commanders(session: Session, scryfall: ScryfallClient) -> int:
    """Work out the commander of decks that have none yet, and fetch the picture of every commander
    that has none. Returns the number of pictures found.

    A guess from the deck name only counts when Scryfall says the card is a legendary creature
    or planeswalker ("Fire Nation Attacks" is a deck name and a spell, not a commander)."""
    decks = session.exec(select(Deck)).all()
    guessed: set[int] = set()
    for deck in decks:
        if deck.commander is None:
            entries = session.exec(select(DeckEntry).where(DeckEntry.deck_id == deck.id)).all()
            guess = _guess_commander(deck, entries)
            if guess:
                deck.commander = guess
                guessed.add(deck.id)
    pending = [deck for deck in decks if deck.commander and deck.commander_image is None]
    if not pending:
        session.commit()
        return 0
    images: dict[str, str] = {}
    for card in session.exec(select(Card).where(Card.image_url != None)).all():  # noqa: E711
        for key in _names(card.name):
            images.setdefault(key, card.image_url)
    # By the front face: Scryfall does not find a double-faced card by both names together.
    lookups = {name_key(deck.commander): {"name": deck.commander.split(" // ")[0]} for deck in pending
               if deck.id in guessed or not _names(deck.commander) & images.keys()}
    commanders: set[str] = set()  # names Scryfall says can be a commander
    if lookups:
        found, _ = await scryfall.get_collection(list(lookups.values()))
        for data in found:
            for key in _names(data.name):
                if data.image_url:
                    images.setdefault(key, data.image_url)
                if _can_command(data.type_line):
                    commanders.add(key)
    for deck in pending:
        if deck.id in guessed and not _names(deck.commander) & commanders:
            deck.commander = ""  # the deck name matched a card that cannot be a commander: choose one yourself
            deck.commander_image = None
            session.add(deck)
    pending = [deck for deck in pending if deck.commander]
    filled = 0
    for deck in pending:
        # The printing the deck list names comes first: you may own the card in several versions.
        image = deck_list_commander_image(session, deck)
        image = image or next((images[key] for key in _names(deck.commander) if key in images), None)
        deck.commander_image = image or ""  # "": looked for, nothing found
        session.add(deck)
        filled += 1 if image else 0
    session.commit()
    return filled


def set_testing(session: Session, deck: Deck, testing: bool) -> None:
    """Mark a deck as one you are only testing: it takes no copies from the collection."""
    deck.testing = testing
    session.add(deck)
    session.flush()


def deck_folders(session: Session) -> list[DeckFolder]:
    return list(session.exec(select(DeckFolder).order_by(DeckFolder.name)))


def _folder_name(session: Session, name: str, own_id: int | None = None) -> str:
    name = " ".join(name.split())
    if not name:
        raise DeckError("A folder needs a name")
    for folder in deck_folders(session):
        if folder.id != own_id and folder.name.casefold() == name.casefold():
            raise DeckError(f"There is a folder called {folder.name} already")
    return name


def create_folder(session: Session, name: str) -> DeckFolder:
    folder = DeckFolder(name=_folder_name(session, name))
    session.add(folder)
    session.flush()
    return folder


def rename_folder(session: Session, folder: DeckFolder, name: str) -> None:
    folder.name = _folder_name(session, name, folder.id)
    session.add(folder)
    session.flush()


def delete_folder(session: Session, folder: DeckFolder) -> int:
    """Remove a folder; the decks in it stay, in no folder. Returns how many decks that were."""
    inside = session.exec(select(Deck).where(Deck.folder_id == folder.id)).all()
    for deck in inside:
        deck.folder_id = None
        session.add(deck)
    session.delete(folder)
    session.flush()
    return len(inside)


def set_folder(session: Session, deck: Deck, folder_id: int | None) -> None:
    """Put a deck in a folder (None: take it out of its folder)."""
    if folder_id is not None and session.get(DeckFolder, folder_id) is None:
        raise DeckError("That folder does not exist")
    deck.folder_id = folder_id
    session.add(deck)
    session.flush()


def set_uses_tokens(session: Session, deck: Deck, uses_tokens: bool | None) -> None:
    """Mark a deck as one that uses tokens (or not); None goes back to what its deck list says."""
    deck.uses_tokens = uses_tokens
    session.add(deck)
    session.flush()


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

def deck_value_history(session: Session, deck: Deck) -> dict:
    """What the copies this deck uses now were worth on every day with prices: chart-ready
    {"labels": [dates], "values": [euros]}. A card without a price that day counts at its last known price."""
    copies: list[tuple[int, bool, int]] = [
        (row.entry.card_id, row.entry.is_foil, count)
        for deck_row in _CardPool(session).rows(deck_id=deck.id)
        for row, count in deck_row.picks
    ]
    card_ids = {card_id for card_id, _, _ in copies}
    if not card_ids:
        return {"labels": [], "values": []}
    snapshots = session.exec(
        select(PriceSnapshot)
        .where(PriceSnapshot.source == SCRYFALL, PriceSnapshot.card_id.in_(card_ids))
        .order_by(PriceSnapshot.date)
    ).all()
    labels, values, last = [], [], {}
    for day, group in itertools.groupby(snapshots, key=lambda snapshot: snapshot.date):
        for snapshot in group:
            last[snapshot.card_id] = snapshot
        labels.append(day.isoformat())
        values.append(round(sum(count * (price_for(last.get(card_id), foil) or 0.0) for card_id, foil, count in copies), 2))
    return {"labels": labels, "values": values}


def saved_deck_rows(session: Session, deck: Deck) -> list[DeckRow]:
    return _CardPool(session).rows(deck_id=deck.id)


def deck_summaries(session: Session) -> list[DeckSummary]:
    pool = _CardPool(session)
    basics: dict[int, int] = {}
    for row in session.exec(select(DeckBasicLand)):
        basics[row.deck_id] = basics.get(row.deck_id, 0) + row.quantity
    tokens: dict[int, int] = {}
    for row in session.exec(select(DeckSkipped).where(DeckSkipped.kind == TOKEN)):
        tokens[row.deck_id] = tokens.get(row.deck_id, 0) + row.quantity
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
                tokens=tokens.get(deck.id, 0),
            )
        )
    return summaries


async def add_missing_to_wishlist(
    session: Session, scryfall: ScryfallClient, cards: list[str | CardRef], target_pct: float = 90.0
) -> tuple[int, list[str]]:
    """Put cards on the wishlist with a target of `target_pct`% of today's price.

    A card can be a name, or a CardRef naming the printing the deck asks for; then
    that printing is the one that is watched. Returns (number added, names
    Scryfall could not find).
    """
    added, not_found = 0, []
    refs = [card if isinstance(card, CardRef) else CardRef(name=card) for card in cards]
    resolved = await scryfall.resolve(refs)
    await scryfall.fill_missing_prices([data for data in resolved if data is not None])
    for ref, data in zip(refs, resolved):
        if data is None:
            not_found.append(ref.name)
            continue
        card = store_scryfall_card(session, data)
        target = round((data.price_eur or 0.0) * target_pct / 100, 2)
        add_to_wishlist(session, card, target, data.cardmarket_url)
        added += 1
    return added, not_found
