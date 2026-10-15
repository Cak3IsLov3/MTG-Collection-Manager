"""Aggregates for the dashboard and the daily totals."""

import datetime as dt
from dataclasses import dataclass

from sqlmodel import Session, select

from app.models import Card, CollectionEntry, DailyTotal, Deck, Game, PriceRiseAlert, PriceSnapshot, utcnow
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


def find_price_rises(
    session: Session, days: int, min_pct: float, min_eur: float, rows: list[CollectionRow] | None = None
) -> list[Mover]:
    """Cards you own whose price went up at least `min_pct` percent and `min_eur` euro in `days` days."""
    rows = collection_rows(session) if rows is None else rows
    latest = latest_snapshots(session, SCRYFALL)
    reference = earliest_snapshots(session, SCRYFALL, dt.date.today() - dt.timedelta(days=days))
    rises: dict[tuple[int, bool], Mover] = {}
    for row in rows:
        new = price_for(latest.get(row.card.id), row.entry.is_foil)
        old = price_for(reference.get(row.card.id), row.entry.is_foil)
        if new is None or not old or new - old < min_eur or (new - old) / old * 100 < min_pct:
            continue
        rises[(row.card.id, row.entry.is_foil)] = Mover(row.card, row.entry.is_foil, old, new)
    return sorted(rises.values(), key=lambda mover: mover.change, reverse=True)


def recent_price_rise_alerts(session: Session, limit: int = 10) -> list[tuple[PriceRiseAlert, Card]]:
    statement = (
        select(PriceRiseAlert, Card)
        .join(Card, PriceRiseAlert.card_id == Card.id)
        .order_by(PriceRiseAlert.sent_at.desc(), PriceRiseAlert.new_price.desc())
        .limit(limit)
    )
    return list(session.exec(statement))


def weekly_summary(session: Session, today: dt.date | None = None) -> dict:
    """The week in short, for the dashboard and the weekly Discord message."""
    today = today or dt.date.today()
    week_ago = today - dt.timedelta(days=7)
    rows = collection_rows(session)
    value = collection_value(rows)
    before = session.exec(
        select(DailyTotal).where(DailyTotal.date <= week_ago).order_by(DailyTotal.date.desc())
    ).first() or session.exec(select(DailyTotal).order_by(DailyTotal.date)).first()
    risers, fallers = movers(session, rows, days=7, limit=3)
    games = list(session.exec(select(Game).where(Game.played_on > week_ago)))
    names = {deck.id: deck.name for deck in session.exec(select(Deck))}
    per_deck: dict[str, list[int]] = {}
    for game in games:
        played = per_deck.setdefault(names.get(game.deck_id, game.deck_name), [0, 0])
        played[0] += 1
        played[1] += game.result == "win"
    since = utcnow() - dt.timedelta(days=7)
    new_copies = sum(
        entry.quantity for entry in session.exec(select(CollectionEntry).where(CollectionEntry.added_at >= since))
    )
    wishlist = wishlist_rows(session)
    return {
        "from": week_ago.isoformat(),
        "to": today.isoformat(),
        "collection_value": value,
        "value_before": before.collection_value if before and before.date < today else None,
        "wishlist_cost": wishlist_cost(wishlist),
        "wishlist_hits": sum(1 for row in wishlist if row.below_target),
        "risers": risers,
        "fallers": fallers,
        "games": len(games),
        "wins": sum(game.result == "win" for game in games),
        "decks_played": sorted(per_deck.items(), key=lambda item: -item[1][0]),
        "new_copies": new_copies,
        "price_rises": len(session.exec(select(PriceRiseAlert).where(PriceRiseAlert.sent_at >= since)).all()),
    }


def weekly_lines(summary: dict) -> list[str]:
    """The weekly summary as short lines (Discord markdown)."""
    value, before = summary["collection_value"], summary["value_before"]
    change = ""
    if before:
        diff = value - before
        change = f" ({'+' if diff >= 0 else '−'}€{abs(diff):.2f}, {diff / before * 100:+.1f}% this week)"
    lines = [f"**Collection** €{value:,.2f}{change}",
             f"**Wishlist** €{summary['wishlist_cost']:,.2f} still to buy · {summary['wishlist_hits']} at or below target"]
    if summary["new_copies"]:
        lines.append(f"**New cards** {summary['new_copies']} added this week")
    if summary["risers"]:
        lines.append("**Risers** " + " · ".join(f"{m.card.name} +€{m.change:.2f}" for m in summary["risers"]))
    if summary["fallers"]:
        lines.append("**Fallers** " + " · ".join(f"{m.card.name} −€{abs(m.change):.2f}" for m in summary["fallers"]))
    if summary["games"]:
        decks = ", ".join(f"{name} {played}×" for name, (played, _) in summary["decks_played"][:3])
        lines.append(f"**Games** {summary['games']} played, {summary['wins']} won · {decks}")
    else:
        lines.append("**Games** none logged this week")
    return lines


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
        "rise_alerts": recent_price_rise_alerts(session),
        "week": weekly_summary(session),
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
