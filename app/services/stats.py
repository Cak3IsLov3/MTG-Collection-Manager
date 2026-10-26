"""Aggregates for the dashboard and the daily totals."""

import datetime as dt
from dataclasses import dataclass

from sqlmodel import Session, select

from app.models import (
    Card, CollectionEntry, DailyTotal, Deck, Game, PriceDropAlert, PriceRiseAlert, PriceSnapshot, WishlistEntry, utcnow,
)
from app.services.collection import CollectionRow, collection_rows, collection_value, purchase_summary
from app.services.pricing import SCRYFALL, earliest_snapshots, latest_snapshots, price_for
from app.services.wishlist import WishlistRow, recent_alerts, wishlist_cost, wishlist_rows


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


def find_price_drops(
    session: Session, days: int, min_pct: float, min_eur: float, rows: list[WishlistRow] | None = None
) -> list[tuple[WishlistRow, Mover]]:
    """Wishlist cards whose price went down at least `min_pct` percent and `min_eur` euro in `days` days."""
    rows = wishlist_rows(session) if rows is None else rows
    latest = latest_snapshots(session, SCRYFALL)
    reference = earliest_snapshots(session, SCRYFALL, dt.date.today() - dt.timedelta(days=days))
    drops = []
    for row in rows:
        new = price_for(latest.get(row.card.id), False)
        old = price_for(reference.get(row.card.id), False)
        if new is None or not old or old - new < min_eur or (old - new) / old * 100 < min_pct:
            continue
        drops.append((row, Mover(row.card, False, old, new)))
    return sorted(drops, key=lambda item: item[1].change_pct)


def recent_price_drop_alerts(session: Session, limit: int = 10) -> list[tuple[PriceDropAlert, Card, WishlistEntry]]:
    statement = (
        select(PriceDropAlert, Card, WishlistEntry)
        .join(Card, PriceDropAlert.card_id == Card.id)
        .join(WishlistEntry, PriceDropAlert.wishlist_entry_id == WishlistEntry.id)
        .order_by(PriceDropAlert.sent_at.desc(), PriceDropAlert.new_price)
        .limit(limit)
    )
    return list(session.exec(statement))


def recent_price_rise_alerts(session: Session, limit: int = 10) -> list[tuple[PriceRiseAlert, Card]]:
    statement = (
        select(PriceRiseAlert, Card)
        .join(Card, PriceRiseAlert.card_id == Card.id)
        .order_by(PriceRiseAlert.sent_at.desc(), PriceRiseAlert.new_price.desc())
        .limit(limit)
    )
    return list(session.exec(statement))


COLOUR_NAMES = {"W": "White", "U": "Blue", "B": "Black", "R": "Red", "G": "Green"}


def colour_spread(session: Session) -> dict:
    """How the collection is spread over the five colours: copies, different cards and value per colour (a
    card of two colours counts for both), and the cards that are colourless, multicolour or not looked up yet."""
    per = {letter: {"copies": 0, "names": set(), "value": 0.0} for letter in COLOUR_NAMES}
    multi = colourless = unknown = copies = 0
    for row in collection_rows(session):
        quantity = row.entry.quantity
        copies += quantity
        have = row.card.colors
        if have is None:
            unknown += quantity
            continue
        if not have:
            colourless += quantity
        elif len(have) > 1:
            multi += quantity
        worth = (row.unit_price or 0.0) * quantity
        for letter in have:
            if letter in per:
                per[letter]["copies"] += quantity
                per[letter]["names"].add(row.card.name)
                per[letter]["value"] += worth
    biggest = max((entry["copies"] for entry in per.values()), default=0)
    colours = [
        {"letter": letter, "name": COLOUR_NAMES[letter], "copies": entry["copies"], "unique": len(entry["names"]),
         "value": round(entry["value"], 2), "share": round(entry["copies"] / copies * 100) if copies else 0,
         "bar": round(entry["copies"] / biggest * 100) if biggest else 0}
        for letter, entry in per.items()
    ]
    ranked = sorted(colours, key=lambda colour: (colour["copies"], colour["letter"]))
    return {"colours": colours, "copies": copies, "multicolour": multi, "colourless": colourless, "unknown": unknown,
            "weakest": ranked[0] if copies and biggest else None, "strongest": ranked[-1] if copies and biggest else None}


def deck_value_moves(session: Session, days: int = 7, limit: int = 3) -> tuple[list[tuple[str, float]], list[tuple[str, float]]]:
    """The decks whose copies gained and lost the most value over the last `days` days: (name, euro) each."""
    from app.services.decks import deck_usage

    latest = latest_snapshots(session, SCRYFALL)
    reference = earliest_snapshots(session, SCRYFALL, dt.date.today() - dt.timedelta(days=days))
    used = deck_usage(session)
    change: dict[str, float] = {}
    for row in collection_rows(session):
        new = price_for(latest.get(row.card.id), row.entry.is_foil)
        old = price_for(reference.get(row.card.id), row.entry.is_foil)
        if new is None or not old or new == old:
            continue
        for deck, copies in used.get(row.entry.id, []):
            change[deck.name] = change.get(deck.name, 0.0) + (new - old) * copies
    ordered = sorted(((name, round(value, 2)) for name, value in change.items() if abs(value) >= 0.005), key=lambda item: item[1])
    return [item for item in reversed(ordered) if item[1] > 0][:limit], [item for item in ordered if item[1] < 0][:limit]


def weekly_summary(session: Session, today: dt.date | None = None, days: int = 7) -> dict:
    """The week (or, with `days`, any number of days) in short, for the dashboard and the Discord message."""
    today = today or dt.date.today()
    week_ago = today - dt.timedelta(days=days)
    rows = collection_rows(session)
    value = collection_value(rows)
    before = session.exec(
        select(DailyTotal).where(DailyTotal.date <= week_ago).order_by(DailyTotal.date.desc())
    ).first() or session.exec(select(DailyTotal).order_by(DailyTotal.date)).first()
    risers, fallers = movers(session, rows, days=days, limit=3)
    deck_gains, deck_losses = deck_value_moves(session, days=days)
    games = list(session.exec(select(Game).where(Game.played_on > week_ago)))
    names = {deck.id: deck.name for deck in session.exec(select(Deck))}
    per_deck: dict[str, list[int]] = {}
    for game in games:
        played = per_deck.setdefault(names.get(game.deck_id, game.deck_name), [0, 0])
        played[0] += 1
        played[1] += game.result == "win"
    since = utcnow() - dt.timedelta(days=days)
    added = list(session.exec(select(CollectionEntry).where(CollectionEntry.added_at >= since)))
    new_copies = sum(entry.quantity for entry in added)
    by_id = {row.entry.id: row for row in rows}
    newest = sorted(((by_id[e.id].card.name, e.quantity, (by_id[e.id].unit_price or 0.0) * e.quantity) for e in added if e.id in by_id),
                    key=lambda item: -item[2])[:3]
    priced = [row for row in rows if row.unit_price is not None]
    top = max(priced, key=lambda row: row.unit_price, default=None)
    wishlist = wishlist_rows(session)
    return {
        "from": week_ago.isoformat(),
        "to": today.isoformat(),
        "days": days,
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
        "new_cards": [{"name": name, "quantity": quantity, "value": round(value, 2)} for name, quantity, value in newest],
        "price_rises": len(session.exec(select(PriceRiseAlert).where(PriceRiseAlert.sent_at >= since)).all()),
        "price_drops": len(session.exec(select(PriceDropAlert).where(PriceDropAlert.sent_at >= since)).all()),
        "deck_gains": [{"name": name, "change": change} for name, change in deck_gains],
        "deck_losses": [{"name": name, "change": change} for name, change in deck_losses],
        "top_card": {"name": top.card.name, "price": top.unit_price, "id": top.card.id} if top else None,
    }


def weekly_lines(summary: dict) -> list[str]:
    """The weekly summary as short lines (Discord markdown)."""
    value, before = summary["collection_value"], summary["value_before"]
    when = "today" if summary.get("days") == 1 else "this week"
    change = ""
    if before:
        diff = value - before
        change = f" ({'+' if diff >= 0 else '−'}€{abs(diff):.2f}, {diff / before * 100:+.1f}% {when})"
    lines = [f"**Collection** €{value:,.2f}{change}",
             f"**Wishlist** €{summary['wishlist_cost']:,.2f} still to buy · {summary['wishlist_hits']} at or below target"]
    if summary["new_copies"]:
        best = ", ".join(f"{c['name']}{f' ×{c['quantity']}' if c['quantity'] > 1 else ''}" for c in summary.get("new_cards", []))
        lines.append(f"**New cards** {summary['new_copies']} added {when}" + (f" · most valuable: {best}" if best else ""))
    if summary.get("deck_gains") or summary.get("deck_losses"):
        parts = [f"{d['name']} +€{d['change']:.2f}" for d in summary.get("deck_gains", [])]
        parts += [f"{d['name']} −€{abs(d['change']):.2f}" for d in summary.get("deck_losses", [])]
        lines.append("**Decks** " + " · ".join(parts))
    if summary["risers"]:
        lines.append("**Risers** " + " · ".join(f"{m.card.name} +€{m.change:.2f}" for m in summary["risers"]))
    if summary["fallers"]:
        lines.append("**Fallers** " + " · ".join(f"{m.card.name} −€{abs(m.change):.2f}" for m in summary["fallers"]))
    if summary["games"]:
        decks = ", ".join(f"{name} {played}×" for name, (played, _) in summary["decks_played"][:3])
        lines.append(f"**Games** {summary['games']} played, {summary['wins']} won · {decks}")
    else:
        lines.append(f"**Games** none logged {when}")
    alerts = [f"{summary['price_drops']} wishlist price drop{'s' if summary['price_drops'] != 1 else ''}"] if summary.get("price_drops") else []
    if summary.get("price_rises"):
        alerts.append(f"{summary['price_rises']} price rise{'s' if summary['price_rises'] != 1 else ''} in your collection")
    if alerts:
        lines.append("**Alerts** " + " · ".join(alerts))
    if summary.get("top_card"):
        lines.append(f"**Most valuable card** {summary['top_card']['name']} €{summary['top_card']['price']:.2f}")
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
        "drop_alerts": recent_price_drop_alerts(session),
        "purchases": purchase_summary(rows),
        "week": weekly_summary(session),
        "colour_spread": colour_spread(session),
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


def dashboard_hero(session: Session) -> dict | None:
    """The banner of the dashboard: the deck you played last (with its commander art), your week and
    your newest achievement. None until you have logged a game."""
    import datetime as dt

    from app.models import Deck, Game
    from app.services.achievements import achievements

    games = sorted(session.exec(select(Game)), key=lambda g: (g.played_on, g.id or 0), reverse=True)
    if not games:
        return None
    last = next((g for g in games if g.deck_id is not None and session.get(Deck, g.deck_id)), games[0])
    deck = session.get(Deck, last.deck_id) if last.deck_id is not None else None
    week_start = dt.date.today() - dt.timedelta(days=6)
    week = [g for g in games if g.played_on >= week_start]
    overall, per_deck = achievements(session)
    unlocked = [a for a in overall + [a for items in per_deck.values() for a in items] if a.unlocked]
    newest = max(unlocked, key=lambda a: a.unlocked_on) if unlocked else None
    return {
        "deck": deck, "deck_name": deck.name if deck else last.deck_name, "played_on": last.played_on,
        "result": last.result, "week_wins": sum(g.result == "win" for g in week),
        "week_losses": sum(g.result == "loss" for g in week), "week_games": len(week),
        "achievement": newest, "unlocked": len(unlocked),
    }
