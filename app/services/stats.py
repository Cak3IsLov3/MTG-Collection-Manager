"""Aggregates for the dashboard and the daily totals."""

import datetime as dt
from dataclasses import dataclass

from sqlmodel import Session, select

from app.models import Card, DailyTotal, PriceSnapshot
from app.services.collection import CollectionRow, collection_rows, collection_value
from app.services.pricing import SCRYFALL, earliest_snapshots, latest_snapshots, price_for
from app.services.wishlist import recent_alerts, wishlist_cost, wishlist_rows


@dataclass
class Mover:
    card: Card
    is_foil: bool
    old_price: float
    new_price: float

    @property
    def change(self) -> float:
        return round(self.new_price - self.old_price, 2)

    @property
    def change_pct(self) -> float:
        return round((self.new_price - self.old_price) / self.old_price * 100, 1)


def record_daily_total(session: Session, day: dt.date | None = None) -> DailyTotal:
    day = day or dt.date.today()
    total = session.exec(select(DailyTotal).where(DailyTotal.date == day)).first() or DailyTotal(date=day)
    total.collection_value = collection_value(collection_rows(session))
    total.wishlist_cost = wishlist_cost(wishlist_rows(session))
    session.add(total)
    session.flush()
    return total


def movers(
    session: Session, rows: list[CollectionRow], days: int = 7, limit: int = 5
) -> tuple[list[Mover], list[Mover]]:
    """Biggest risers and fallers in the collection over the last `days` days."""
    latest = latest_snapshots(session, SCRYFALL)
    reference = earliest_snapshots(session, SCRYFALL, dt.date.today() - dt.timedelta(days=days))
    changed: dict[tuple[int, bool], Mover] = {}
    for row in rows:
        new = price_for(latest.get(row.card.id), row.entry.is_foil)
        old = price_for(reference.get(row.card.id), row.entry.is_foil)
        if new is None or not old or new == old:
            continue
        changed[(row.card.id, row.entry.is_foil)] = Mover(row.card, row.entry.is_foil, old, new)
    ordered = sorted(changed.values(), key=lambda m: m.change, reverse=True)
    gainers = [m for m in ordered if m.change > 0][:limit]
    losers = [m for m in reversed(ordered) if m.change < 0][:limit]
    return gainers, losers


def dashboard(session: Session) -> dict:
    rows = collection_rows(session)
    wishlist = wishlist_rows(session)
    gainers, losers = movers(session, rows)
    priced = [row for row in rows if row.unit_price is not None]
    history = session.exec(select(DailyTotal).order_by(DailyTotal.date)).all()
    return {
        "collection_value": collection_value(rows),
        "wishlist_cost": wishlist_cost(wishlist),
        "card_count": sum(row.entry.quantity for row in rows),
        "unique_count": len(rows),
        "unpriced_count": len(rows) - len(priced),
        "wishlist_count": len(wishlist),
        "wishlist_hits": sum(1 for row in wishlist if row.below_target),
        "top_cards": sorted(priced, key=lambda row: row.unit_price, reverse=True)[:10],
        "gainers": gainers,
        "losers": losers,
        "alerts": recent_alerts(session),
        "history": {
            "labels": [total.date.isoformat() for total in history],
            "collection": [total.collection_value for total in history],
            "wishlist": [total.wishlist_cost for total in history],
        },
    }


def price_history(session: Session, card_id: int) -> dict:
    """Chart-ready price series for one card (None where a source has no price that day)."""
    snapshots = session.exec(
        select(PriceSnapshot).where(PriceSnapshot.card_id == card_id).order_by(PriceSnapshot.date)
    ).all()
    labels = sorted({snapshot.date for snapshot in snapshots})
    by_key = {(snapshot.source, snapshot.date): snapshot for snapshot in snapshots}

    def series(source: str, attribute: str) -> list[float | None]:
        return [getattr(by_key.get((source, day)), attribute, None) for day in labels]

    return {
        "labels": [day.isoformat() for day in labels],
        "scryfall": series("scryfall", "price_eur"),
        "scryfall_foil": series("scryfall", "price_eur_foil"),
        "cardmarket": series("cardmarket", "price_eur"),
    }
