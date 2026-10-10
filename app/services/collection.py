"""HAVE: the collection."""

from dataclasses import dataclass, field

from sqlmodel import Session, select

from app.csv_import import ImportRow
from app.models import Card, CollectionEntry
from app.scryfall import ScryfallClient
from app.services.pricing import SCRYFALL, latest_snapshots, price_for, store_scryfall_card


@dataclass
class CollectionRow:
    entry: CollectionEntry
    card: Card
    unit_price: float | None

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


def collection_rows(session: Session, entry_id: int | None = None) -> list[CollectionRow]:
    statement = select(CollectionEntry, Card).join(Card, CollectionEntry.card_id == Card.id).order_by(Card.name)
    if entry_id is not None:
        statement = statement.where(CollectionEntry.id == entry_id)
    prices = latest_snapshots(session, SCRYFALL)
    return [
        CollectionRow(entry=entry, card=card, unit_price=price_for(prices.get(card.id), entry.is_foil))
        for entry, card in session.exec(statement)
    ]


def collection_value(rows: list[CollectionRow]) -> float:
    """Cards without a known price count as 0."""
    return round(sum(row.total_price or 0.0 for row in rows), 2)


async def add_card_by_name(
    session: Session,
    scryfall: ScryfallClient,
    name: str,
    set_code: str | None = None,
    quantity: int = 1,
    is_foil: bool = False,
) -> CollectionEntry:
    card = store_scryfall_card(session, await scryfall.get_by_name(name, set_code))
    return add_to_collection(session, card, quantity, is_foil)


async def import_rows(
    session: Session, scryfall: ScryfallClient, rows: list[ImportRow], replace: bool = False
) -> ImportResult:
    """Add rows to the collection; with `replace` the import becomes the whole collection."""
    result = ImportResult()
    resolved = await scryfall.resolve(rows)
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
