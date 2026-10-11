"""HAVE: the collection."""

import unicodedata
from dataclasses import dataclass, field

from sqlmodel import Session, select

from app.csv_import import ImportRow
from app.models import Card, CollectionEntry
from app.scryfall import InvalidCardRequest, ScryfallCard, ScryfallClient
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
    session.add(entry)
    session.flush()
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
    """Add rows to the collection; with `replace` the import becomes the whole collection."""
    result = ImportResult()
    resolved = await scryfall.resolve(rows)
    await scryfall.fill_missing_prices(
        [data for data in resolved if data is not None],
        {data.scryfall_id for row, data in zip(rows, resolved) if data is not None and row.is_foil},
    )
    if replace:
        # Only after Scryfall answered, so a failed lookup never empties the collection.
        for entry in session.exec(select(CollectionEntry)).all():
            session.delete(entry)
        session.flush()
    for row, data in zip(rows, resolved):
        if data is None:
            result.not_found.append(row.name)
            continue
        add_to_collection(session, store_scryfall_card(session, data), row.quantity, row.is_foil)
        result.imported_rows += 1
        result.imported_cards += row.quantity
    return result
