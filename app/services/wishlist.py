"""WANT: the wishlist and its alerts."""

import datetime as dt
from dataclasses import dataclass

from sqlmodel import Session, select

from app.models import Alert, Card, CollectionEntry, WishlistEntry
from app.scryfall import ScryfallClient, name_key
from app.services.collection import add_to_collection
from app.services.pricing import CARDMARKET, SCRYFALL, latest_snapshots, store_scryfall_card


@dataclass
class WishlistRow:
    entry: WishlistEntry
    card: Card
    current_price: float | None
    price_source: str | None
    price_date: dt.date | None
    # Set code when this printing has no price and the price of another printing is shown.
    price_from_set: str | None = None

    @property
    def below_target(self) -> bool:
        return self.current_price is not None and self.current_price <= self.entry.target_price

    @property
    def difference(self) -> float | None:
        """How far the current price is above (+) or below (-) the target."""
        if self.current_price is None:
            return None
        return round(self.current_price - self.entry.target_price, 2)


def add_to_wishlist(
    session: Session, card: Card, target_price: float, cardmarket_url: str | None = None
) -> WishlistEntry:
    """Add a card, or update the target of the entry that is already watching it."""
    entry = session.exec(
        select(WishlistEntry).where(WishlistEntry.card_id == card.id, WishlistEntry.active == True)  # noqa: E712
    ).first()
    if entry is None:
        entry = WishlistEntry(card_id=card.id, target_price=target_price)
    entry.target_price = target_price
    if cardmarket_url:
        entry.cardmarket_url = cardmarket_url
    session.add(entry)
    session.flush()
    return entry


def current_prices(session: Session) -> dict[int, tuple[float, str, dt.date, str | None]]:
    """Freshest known price per card id; Cardmarket wins over Scryfall on the same day.

    The last element is the set the price was borrowed from, if it is not the printing's own.
    """
    best: dict[int, tuple[float, str, dt.date, str | None]] = {}
    for source in (SCRYFALL, CARDMARKET):
        for card_id, snapshot in latest_snapshots(session, source).items():
            if snapshot.price_eur is None:
                continue
            if card_id not in best or snapshot.date >= best[card_id][2]:
                best[card_id] = (snapshot.price_eur, source, snapshot.date, snapshot.eur_from_set)
    return best


def wishlist_rows(session: Session, entry_id: int | None = None) -> list[WishlistRow]:
    statement = select(WishlistEntry, Card).join(Card, WishlistEntry.card_id == Card.id).order_by(Card.name)
    if entry_id is not None:
        statement = statement.where(WishlistEntry.id == entry_id)
    else:
        statement = statement.where(WishlistEntry.active == True)  # noqa: E712
    prices = current_prices(session)
    rows = []
    for entry, card in session.exec(statement):
        price, source, day, from_set = prices.get(card.id, (None, None, None, None))
        rows.append(
            WishlistRow(
                entry=entry, card=card, current_price=price, price_source=source, price_date=day,
                price_from_set=from_set,
            )
        )
    return rows


def wishlist_cost(rows: list[WishlistRow]) -> float:
    """What it costs to buy every active wishlist card at today's prices."""
    return round(sum(row.current_price or 0.0 for row in rows if row.entry.active), 2)


async def add_wish_by_name(
    session: Session,
    scryfall: ScryfallClient,
    name: str,
    target_price: float,
    set_code: str | None = None,
    cardmarket_url: str | None = None,
) -> WishlistEntry:
    data = await scryfall.get_by_name(name, set_code)
    await scryfall.fill_missing_prices([data])
    card = store_scryfall_card(session, data)
    return add_to_wishlist(session, card, target_price, cardmarket_url or data.cardmarket_url)


async def change_printing(
    session: Session, scryfall: ScryfallClient, entry: WishlistEntry, scryfall_id: str
) -> WishlistEntry:
    """Watch another printing (set) of the same card; target price and alert history are kept.

    Raises ValueError when the printing is a different card or is already on the wishlist.
    """
    current = session.get(Card, entry.card_id)
    data = await scryfall.get_by_id(scryfall_id)
    if name_key(data.name) != name_key(current.name):
        raise ValueError(f"{data.name} is not a printing of {current.name}")
    await scryfall.fill_missing_prices([data])
    card = store_scryfall_card(session, data)
    duplicate = session.exec(
        select(WishlistEntry).where(
            WishlistEntry.card_id == card.id, WishlistEntry.active == True, WishlistEntry.id != entry.id  # noqa: E712
        )
    ).first()
    if duplicate is not None:
        raise ValueError(f"{card.name} from {card.set_name} is already on your wishlist")
    entry.card_id = card.id
    if data.cardmarket_url:
        entry.cardmarket_url = data.cardmarket_url
    session.add(entry)
    session.flush()
    return entry


def clear_wishlist(session: Session) -> int:
    """Stop watching every card; returns how many were removed. Alert history is kept."""
    entries = session.exec(select(WishlistEntry).where(WishlistEntry.active == True)).all()  # noqa: E712
    for entry in entries:
        entry.active = False
        session.add(entry)
    session.flush()
    return len(entries)


def set_targets(session: Session, targets: dict[int, float]) -> int:
    """Set the target price of several wishlist entries at once; returns how many actually changed.

    Raises ValueError (and changes nothing) when an entry is unknown or a price is negative.
    """
    entries = {}
    for entry_id, price in targets.items():
        entry = session.get(WishlistEntry, entry_id)
        if entry is None or not entry.active:
            raise ValueError("One of those cards is no longer on your wishlist")
        if price < 0:
            raise ValueError("A target price cannot be negative")
        entries[entry_id] = entry
    changed = 0
    for entry_id, entry in entries.items():
        price = round(targets[entry_id], 2)
        if entry.target_price != price:
            entry.target_price = price
            session.add(entry)
            changed += 1
    session.flush()
    return changed


def purchase(
    session: Session, entry: WishlistEntry, quantity: int = 1, is_foil: bool = False, price: float | None = None
) -> CollectionEntry:
    """WANT -> HAVE: move a bought card (for `price` per copy, if given) into the collection and stop watching it."""
    card = session.get(Card, entry.card_id)
    collection_entry = add_to_collection(session, card, quantity, is_foil, price)
    entry.active = False  # kept (inactive) so the alert history stays intact
    session.add(entry)
    session.flush()
    return collection_entry


def recently_alerted(session: Session, entry_id: int, now: dt.datetime, cooldown_hours: int) -> bool:
    cutoff = now - dt.timedelta(hours=cooldown_hours)
    recent = session.exec(
        select(Alert).where(Alert.wishlist_entry_id == entry_id, Alert.sent_at > cutoff)
    ).first()
    return recent is not None


def recent_alerts(session: Session, limit: int = 10) -> list[tuple[Alert, WishlistEntry, Card]]:
    statement = (
        select(Alert, WishlistEntry, Card)
        .join(WishlistEntry, Alert.wishlist_entry_id == WishlistEntry.id)
        .join(Card, WishlistEntry.card_id == Card.id)
        .order_by(Alert.sent_at.desc())
        .limit(limit)
    )
    return list(session.exec(statement))
