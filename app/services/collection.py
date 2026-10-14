"""HAVE: the collection."""

import unicodedata
from dataclasses import dataclass, field

from sqlmodel import Session, select

from app.csv_import import ImportRow
from app.models import Card, CollectionEntry, DeckPick
from app.scryfall import InvalidCardRequest, ScryfallCard, ScryfallClient, ScryfallError
from app.services.pricing import SCRYFALL, latest_snapshots, price_for, price_origin, store_scryfall_card


@dataclass
class CollectionRow:
    entry: CollectionEntry
    card: Card
    unit_price: float | None
    # Set code when this printing has no price and the price of another printing is shown.
    price_from_set: str | None = None

    @property
    def total_price(self) -> float | None:
        return None if self.unit_price is None else round(self.unit_price * self.entry.quantity, 2)


@dataclass
class ImportResult:
    imported_rows: int = 0
    imported_cards: int = 0
    not_found: list[str] = field(default_factory=list)
    # With replace: cards you added by hand that the file does not have, and so were kept.
    kept_manual: int = 0
    # Copies that are new in the collection; they wait for you to sort them into decks.
    new_copies: int = 0


def add_to_collection(session: Session, card: Card, quantity: int = 1, is_foil: bool = False) -> CollectionEntry:
    """Add copies of a card; the same printing + finish is merged into one entry."""
    entry = session.exec(
        select(CollectionEntry).where(CollectionEntry.card_id == card.id, CollectionEntry.is_foil == is_foil)
    ).first()
    if entry is None:
        entry = CollectionEntry(card_id=card.id, quantity=0, is_foil=is_foil)
    entry.quantity += quantity
    session.add(entry)
    session.flush()
    return entry


def set_quantity(session: Session, entry: CollectionEntry, quantity: int) -> CollectionEntry:
    """Change how many copies of a printing are in the collection (at least one; delete to remove)."""
    if quantity < 1:
        raise ValueError("Quantity must be at least 1")
    entry.quantity = quantity
    entry.unsorted = min(entry.unsorted, quantity)
    session.add(entry)
    session.flush()
    return entry


def set_foil(session: Session, entry: CollectionEntry, is_foil: bool) -> CollectionEntry:
    """Mark the copies of an entry as foil or non-foil; returns the entry that holds them afterwards.

    If the collection already has this printing in the other finish, the two are
    merged into that entry, and decks that used these copies follow along.
    """
    if entry.is_foil == is_foil:
        return entry
    other = session.exec(
        select(CollectionEntry).where(
            CollectionEntry.card_id == entry.card_id, CollectionEntry.is_foil == is_foil, CollectionEntry.id != entry.id
        )
    ).first()
    if other is None:
        entry.is_foil = is_foil
        session.add(entry)
        session.flush()
        return entry
    other.quantity += entry.quantity
    other.unsorted += entry.unsorted
    session.add(other)
    for pick in session.exec(select(DeckPick).where(DeckPick.collection_entry_id == entry.id)).all():
        pick.collection_entry_id = other.id
        session.add(pick)
    session.delete(entry)
    session.flush()
    return other


async def change_finish(
    session: Session, scryfall: ScryfallClient, entry: CollectionEntry, is_foil: bool
) -> CollectionEntry:
    """set_foil, and make sure the new finish has a price (borrowed from another printing if need be)."""
    entry = set_foil(session, entry, is_foil)
    snapshot = latest_snapshots(session, SCRYFALL).get(entry.card_id)
    if price_for(snapshot, entry.is_foil) is None:
        card = session.get(Card, entry.card_id)
        try:
            data = await scryfall.get_by_id(card.scryfall_id)
            await scryfall.fill_missing_prices([data], {data.scryfall_id} if entry.is_foil else set())
        except ScryfallError:
            return entry  # the finish is changed; the price follows with the next update
        store_scryfall_card(session, data)
    return entry


def collection_rows(session: Session, entry_id: int | None = None) -> list[CollectionRow]:
    statement = select(CollectionEntry, Card).join(Card, CollectionEntry.card_id == Card.id).order_by(Card.name)
    if entry_id is not None:
        statement = statement.where(CollectionEntry.id == entry_id)
    prices = latest_snapshots(session, SCRYFALL)
    return [
        CollectionRow(
            entry=entry,
            card=card,
            unit_price=price_for(prices.get(card.id), entry.is_foil),
            price_from_set=price_origin(prices.get(card.id), entry.is_foil),
        )
        for entry, card in session.exec(statement)
    ]


def _fold(text: str) -> str:
    """Lower-case without accents, so "lim-dul" finds "Lim-Dûl's Vault"."""
    decomposed = unicodedata.normalize("NFD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).casefold()


COLOR_MATCHES = ("any", "within", "exact")


def color_match(colors: str | None, wanted: str, mode: str = "any") -> bool:
    """Does a card with colour identity `colors` match the chosen colours (letters from WUBRG, C = colourless)?

    any: the card has one of the colours (a colourless card only when C is chosen);
    within: the card fits in a deck of these colours, so colourless cards always do;
    exact: the card has exactly these colours."""
    if colors is None:  # colours not fetched yet
        return False
    have = set(colors)
    chosen = set(wanted.upper()) & set("WUBRGC")
    want = chosen - {"C"}
    if mode == "within":
        return have <= want
    if mode == "exact":
        return have == want
    return bool(have & want) or (not have and "C" in chosen)


def filter_colors(rows: list[CollectionRow], wanted: str | None, mode: str = "any") -> list[CollectionRow]:
    if not wanted:
        return rows
    return [row for row in rows if color_match(row.card.colors, wanted, mode)]


def search_rows(rows: list[CollectionRow], query: str | None) -> list[CollectionRow]:
    """Rows whose card name, set name or set code contain every word of `query`."""
    words = _fold(query or "").split()
    if not words:
        return rows
    return [
        row
        for row in rows
        if all(
            word in _fold(f"{row.card.name} {row.card.set_name} {row.card.set_code} #{row.card.collector_number}")
            for word in words
        )
    ]


def collection_value(rows: list[CollectionRow]) -> float:
    """Cards without a known price count as 0."""
    return round(sum(row.total_price or 0.0 for row in rows), 2)


async def find_card(
    scryfall: ScryfallClient, name: str, set_code: str | None = None, collector_number: str | None = None
) -> ScryfallCard:
    """Look a card up the way the "Add a card" forms describe it: by name, or by set + collector number."""
    if collector_number:
        if not set_code:
            raise InvalidCardRequest("Fill in the set as well when you use a card number")
        data = await scryfall.get_by_number(set_code, collector_number)
        # A name is optional here, but if it is given it has to be the card that number points to.
        if name and _fold(name) not in _fold(data.name):
            raise InvalidCardRequest(
                f"{set_code.upper()} #{collector_number} is {data.name}, not {name}. Check the set and number."
            )
        return data
    if name:
        return await scryfall.get_by_name(name, set_code)
    raise InvalidCardRequest("Fill in a card name, or a set and card number")


async def add_card_by_name(
    session: Session,
    scryfall: ScryfallClient,
    name: str,
    set_code: str | None = None,
    quantity: int = 1,
    is_foil: bool = False,
    collector_number: str | None = None,
) -> CollectionEntry:
    """Add a card by name, or one exact printing by set code + collector number."""
    data = await find_card(scryfall, name, set_code, collector_number)
    await scryfall.fill_missing_prices([data], {data.scryfall_id} if is_foil else set())
    return add_to_collection(session, store_scryfall_card(session, data), quantity, is_foil)


async def import_rows(
    session: Session, scryfall: ScryfallClient, rows: list[ImportRow], replace: bool = False
) -> ImportResult:
    """Add rows to the collection.

    With `replace` the file replaces what earlier imports brought in, while cards added by
    hand stay. A card in the file that you also added by hand counts once: the file's
    quantity is taken over. Entries are updated in place, so decks keep the printings you
    chose for them.
    """
    result = ImportResult()
    resolved = await scryfall.resolve(rows)
    await scryfall.fill_missing_prices(
        [data for data in resolved if data is not None],
        {data.scryfall_id for row, data in zip(rows, resolved) if data is not None and row.is_foil},
    )
    # Only now, after Scryfall answered, is anything changed: a failed lookup never empties the collection.
    seen: set[int] = set()  # entries this import has set already
    before: dict[int, int] = {}  # copies an entry had before this import
    for row, data in zip(rows, resolved):
        if data is None:
            result.not_found.append(row.name)
            continue
        card = store_scryfall_card(session, data)
        entry = session.exec(
            select(CollectionEntry).where(CollectionEntry.card_id == card.id, CollectionEntry.is_foil == row.is_foil)
        ).first()
        if entry is None:
            entry = CollectionEntry(card_id=card.id, quantity=0, is_foil=row.is_foil, imported=True)
        elif entry.id not in seen:
            before[entry.id] = entry.quantity
        if replace and entry.id is not None and entry.id not in seen:
            entry.quantity = 0  # the file says how many there are
            entry.imported = True
        entry.quantity += row.quantity
        session.add(entry)
        session.flush()
        before.setdefault(entry.id, 0)
        seen.add(entry.id)
        result.imported_rows += 1
        result.imported_cards += row.quantity
    # Copies the import brought in wait for you to sort them into decks.
    for entry_id in seen:
        entry = session.get(CollectionEntry, entry_id)
        added = entry.quantity - before[entry_id]
        entry.unsorted = min(entry.quantity, max(0, entry.unsorted + max(0, added)))
        result.new_copies += max(0, added)
        session.add(entry)
    if replace:
        for entry in session.exec(select(CollectionEntry)).all():
            if entry.id in seen:
                continue
            if entry.imported:  # brought in by an earlier import and no longer in the file
                for pick in session.exec(select(DeckPick).where(DeckPick.collection_entry_id == entry.id)).all():
                    session.delete(pick)
                session.delete(entry)
            else:
                result.kept_manual += entry.quantity
        session.flush()
    return result
