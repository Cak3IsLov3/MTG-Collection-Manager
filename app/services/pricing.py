"""Cards and their price snapshots."""

import datetime as dt

from sqlalchemy import and_, func
from sqlmodel import Session, select

from app.models import Card, PriceSnapshot
from app.scryfall import ScryfallCard

SCRYFALL = "scryfall"
CARDMARKET = "cardmarket"


def upsert_card(session: Session, data: ScryfallCard) -> Card:
    card = session.exec(select(Card).where(Card.scryfall_id == data.scryfall_id)).first()
    if card is None:
        card = Card(scryfall_id=data.scryfall_id, name=data.name, set_code=data.set_code, set_name=data.set_name)
    card.name = data.name
    card.set_code = data.set_code
    card.set_name = data.set_name
    card.collector_number = data.collector_number
    card.image_url = data.image_url
    if data.colors is not None:
        card.colors = data.colors
    if data.rarity:
        card.rarity = data.rarity
    session.add(card)
    session.flush()
    return card


def record_snapshot(
    session: Session,
    card_id: int,
    source: str,
    price_eur: float | None,
    price_eur_foil: float | None = None,
    day: dt.date | None = None,
    eur_from_set: str | None = None,
    eur_foil_from_set: str | None = None,
) -> PriceSnapshot:
    """Store today's price, overwriting an earlier snapshot from the same day and source."""
    day = day or dt.date.today()
    snapshot = session.exec(
        select(PriceSnapshot).where(
            PriceSnapshot.card_id == card_id, PriceSnapshot.date == day, PriceSnapshot.source == source
        )
    ).first()
    if snapshot is None:
        snapshot = PriceSnapshot(card_id=card_id, date=day, source=source)
    snapshot.price_eur = price_eur
    snapshot.price_eur_foil = price_eur_foil
    snapshot.eur_from_set = eur_from_set
    snapshot.eur_foil_from_set = eur_foil_from_set
    session.add(snapshot)
    session.flush()
    return snapshot


def store_scryfall_card(session: Session, data: ScryfallCard) -> Card:
    """Upsert the card and snapshot its current Scryfall prices."""
    card = upsert_card(session, data)
    record_scryfall_price(session, card.id, data)
    return card


def record_scryfall_price(session: Session, card_id: int, data: ScryfallCard) -> PriceSnapshot:
    """Snapshot today's Scryfall prices, including where a borrowed price came from."""
    return record_snapshot(
        session,
        card_id,
        SCRYFALL,
        data.price_eur,
        data.price_eur_foil,
        eur_from_set=data.eur_from_set,
        eur_foil_from_set=data.eur_foil_from_set,
    )


def _snapshot_per_card(session: Session, source: str, pick, since: dt.date | None) -> dict[int, PriceSnapshot]:
    wanted = select(PriceSnapshot.card_id, pick(PriceSnapshot.date).label("day")).where(
        PriceSnapshot.source == source
    )
    if since is not None:
        wanted = wanted.where(PriceSnapshot.date >= since)
    wanted = wanted.group_by(PriceSnapshot.card_id).subquery()
    statement = (
        select(PriceSnapshot)
        .join(wanted, and_(PriceSnapshot.card_id == wanted.c.card_id, PriceSnapshot.date == wanted.c.day))
        .where(PriceSnapshot.source == source)
    )
    return {snapshot.card_id: snapshot for snapshot in session.exec(statement)}


def latest_snapshots(session: Session, source: str) -> dict[int, PriceSnapshot]:
    """Most recent snapshot per card id."""
    return _snapshot_per_card(session, source, func.max, None)


def earliest_snapshots(session: Session, source: str, since: dt.date) -> dict[int, PriceSnapshot]:
    """Oldest snapshot per card id that is not older than `since`."""
    return _snapshot_per_card(session, source, func.min, since)


def price_origin(snapshot: PriceSnapshot | None, is_foil: bool) -> str | None:
    """Set code the price was borrowed from, or None when it is this printing's own price."""
    if snapshot is None:
        return None
    return snapshot.eur_foil_from_set if is_foil else snapshot.eur_from_set


def price_for(snapshot: PriceSnapshot | None, is_foil: bool) -> float | None:
    if snapshot is None:
        return None
    return snapshot.price_eur_foil if is_foil else snapshot.price_eur
