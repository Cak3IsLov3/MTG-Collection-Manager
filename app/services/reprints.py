"""Reprints of valuable cards you own: a new printing often makes the price drop, so it is a moment
to sell, or to wait with buying more."""

import datetime as dt
from dataclasses import dataclass

from sqlmodel import Session, select

from app.models import Card, ReprintAlert, utcnow
from app.scryfall import ScryfallCard, ScryfallClient
from app.services.collection import collection_rows
from app.services.decks import _names


@dataclass
class Reprint:
    owned: Card  # the printing you have
    price: float  # what it is worth now
    copies: int
    new: ScryfallCard  # the new printing
    released: dt.date | None


def _valuable_cards(session: Session, min_eur: float) -> dict[str, tuple[Card, float, int]]:
    """Per name key: the most valuable printing you own of a card worth at least `min_eur`, and how
    many copies of the card you have."""
    found: dict[str, tuple[Card, float, int]] = {}
    copies: dict[str, int] = {}
    for row in collection_rows(session):
        for key in _names(row.card.name):
            copies[key] = copies.get(key, 0) + row.entry.quantity
        if row.unit_price is None or row.unit_price < min_eur:
            continue
        for key in _names(row.card.name):
            if key not in found or row.unit_price > found[key][1]:
                found[key] = (row.card, row.unit_price, 0)
    return {key: (card, price, copies[key]) for key, (card, price, _) in found.items()}


async def find_reprints(
    session: Session, scryfall: ScryfallClient, days: int, min_eur: float, today: dt.date | None = None
) -> list[Reprint]:
    """New printings, released in the last `days` days or announced for later, of cards you own
    worth at least `min_eur`, which were not reported before."""
    valuable = _valuable_cards(session, min_eur)
    if not valuable:
        return []
    since = (today or dt.date.today()) - dt.timedelta(days=days)
    results = await scryfall.search(f"is:reprint game:paper date>={since.isoformat()}", unique="prints", order="released")
    # Once per card and set: a set often has several versions of a card (borderless, showcase, ...).
    known = {(card_id, set_code) for card_id, set_code in session.exec(select(ReprintAlert.card_id, ReprintAlert.set_code))}
    owned_ids = {card.scryfall_id for card, _, _ in valuable.values()}
    reprints = []
    for data in results:
        keys = _names(data.get("name", ""))
        match = next((valuable[key] for key in keys if key in valuable), None)
        if match is None or data.get("id") in owned_ids:
            continue
        card, price, copies = match
        if (card.id, data.get("set", "")) in known:
            continue
        known.add((card.id, data.get("set", "")))
        released = data.get("released_at")
        reprints.append(Reprint(card, price, copies, ScryfallCard.from_json(data),
                                dt.date.fromisoformat(released) if released else None))
    return sorted(reprints, key=lambda reprint: -reprint.price)


def record_reprints(session: Session, reprints: list[Reprint], notified: bool) -> list[ReprintAlert]:
    alerts = []
    now = utcnow()  # one moment for the whole batch, so the dashboard sorts it by value
    for reprint in reprints:
        alert = ReprintAlert(
            card_id=reprint.owned.id, new_scryfall_id=reprint.new.scryfall_id, set_code=reprint.new.set_code,
            set_name=reprint.new.set_name, released_at=reprint.released, image_url=reprint.new.image_url,
            price=reprint.price, copies=reprint.copies, notified=notified, found_at=now,
        )
        session.add(alert)
        alerts.append(alert)
    session.flush()
    return alerts


def recent_reprint_alerts(session: Session, limit: int = 15) -> list[tuple[ReprintAlert, Card]]:
    statement = (
        select(ReprintAlert, Card).join(Card, ReprintAlert.card_id == Card.id)
        .order_by(ReprintAlert.found_at.desc(), ReprintAlert.price.desc()).limit(limit)
    )
    return list(session.exec(statement))


def _where(reprint: Reprint, today: dt.date) -> str:
    when = ""
    if reprint.released:
        when = f", out {reprint.released.isoformat()}" if reprint.released > today else f", released {reprint.released.isoformat()}"
    return f"{reprint.new.set_name} ({reprint.new.set_code.upper()}{when})"


def reprint_lines(reprints: list[Reprint], today: dt.date | None = None) -> list[str]:
    """One line per card, with every set it is reprinted in."""
    today = today or dt.date.today()
    per_card: dict[int, list[Reprint]] = {}
    for reprint in reprints:
        per_card.setdefault(reprint.owned.id, []).append(reprint)
    return [
        f"**{group[0].owned.name}** · reprinted in {'; '.join(_where(r, today) for r in group)} · "
        f"yours €{group[0].price:.2f} · you have {group[0].copies}"
        for group in per_card.values()
    ]
