"""Background jobs: daily Scryfall price update and the periodic wishlist check."""

import datetime as dt
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlmodel import Session, select

from app import deps
from app.cardmarket import CardmarketError, CardmarketPaused, CardmarketScraper
from app.config import get_settings
from app.database import engine
from app.discord import DiscordNotifier
from app.models import Alert, Card, CollectionEntry, PriceRiseAlert, WishlistEntry, utcnow
from app.scryfall import ScryfallClient, ScryfallError
from app.services.pricing import CARDMARKET, SCRYFALL, latest_snapshots, record_scryfall_price, record_snapshot
from app.services.stats import find_price_rises, record_daily_total, weekly_lines, weekly_summary
from app.services.wishlist import recently_alerted

logger = logging.getLogger(__name__)


def tracked_cards(session: Session) -> list[Card]:
    """Every card that is in the collection or on the active wishlist."""
    owned = select(CollectionEntry.card_id)
    wanted = select(WishlistEntry.card_id).where(WishlistEntry.active == True)  # noqa: E712
    return list(session.exec(select(Card).where(Card.id.in_(owned) | Card.id.in_(wanted))))


async def update_collection_prices(session: Session, scryfall: ScryfallClient) -> int:
    """Snapshot today's Scryfall prices and the daily totals. Returns the number of cards updated."""
    cards = tracked_cards(session)
    found, _ = await scryfall.get_collection([{"id": card.scryfall_id} for card in cards])
    foils = session.exec(
        select(Card.scryfall_id)
        .join(CollectionEntry, CollectionEntry.card_id == Card.id)
        .where(CollectionEntry.is_foil == True)  # noqa: E712
    ).all()
    await scryfall.fill_missing_prices(found, set(foils))
    by_id = {data.scryfall_id: data for data in found}
    updated = 0
    for card in cards:
        data = by_id.get(card.scryfall_id)
        if data is None:
            logger.warning("Scryfall no longer knows %s (%s)", card.name, card.scryfall_id)
            continue
        record_scryfall_price(session, card.id, data)
        if data.colors is not None:
            card.colors = data.colors
        updated += 1
    record_daily_total(session)
    session.commit()
    return updated


async def check_price_rises(
    session: Session,
    notifier: DiscordNotifier,
    now: dt.datetime | None = None,
    min_pct: float | None = None,
    min_eur: float | None = None,
    days: int | None = None,
) -> list[PriceRiseAlert]:
    """After the daily price update: tell Discord which cards you own went up in price.

    A card is mentioned once per period of `days` days, so the same rise is not sent every day.
    """
    from app.services.decks import collection_decks

    settings = get_settings()
    min_pct = settings.price_rise_pct if min_pct is None else min_pct
    min_eur = settings.price_rise_min_eur if min_eur is None else min_eur
    days = settings.price_rise_days if days is None else days
    if min_pct <= 0:
        return []
    now = now or utcnow()
    rises = find_price_rises(session, days, min_pct, min_eur)
    recent = {
        (alert.card_id, alert.is_foil)
        for alert in session.exec(select(PriceRiseAlert).where(PriceRiseAlert.sent_at >= now - dt.timedelta(days=days)))
    }
    rises = [rise for rise in rises if (rise.card.id, rise.is_foil) not in recent]
    if not rises:
        return []
    # How many copies you have of each, and how many no deck uses or needs (to sell, say).
    shared = collection_decks(session)
    owned: dict[tuple[int, bool], list[int]] = {}
    for entry in session.exec(select(CollectionEntry)):
        counts = owned.setdefault((entry.card_id, entry.is_foil), [0, 0])
        in_decks = sum(copies for _, copies in shared.usage.get(entry.id, []))
        counts[0] += entry.quantity
        counts[1] += 0 if shared.wanted.get(entry.id) else max(0, entry.quantity - in_decks)
    alerts, lines = [], []
    for rise in rises:
        alerts.append(PriceRiseAlert(
            card_id=rise.card.id, is_foil=rise.is_foil, sent_at=now, old_price=rise.old_price,
            new_price=rise.new_price, days=days,
        ))
        have, spare = owned.get((rise.card.id, rise.is_foil), [0, 0])
        lines.append(
            f"**{rise.card.name}**{' (foil)' if rise.is_foil else ''} · {rise.card.set_code.upper()} · "
            f"€{rise.old_price:.2f} → €{rise.new_price:.2f} (+{rise.change_pct:.0f}%) · "
            f"you have {have}{f', {spare} spare' if spare else ''}"
        )
    notified = notifier.configured and await notifier.send_price_rises(lines, days)
    for alert in alerts:
        alert.notified = bool(notified)
        session.add(alert)
    session.commit()
    return alerts


async def send_weekly_summary(session: Session, notifier: DiscordNotifier) -> bool:
    """Post the week in short to Discord: collection value, risers and fallers, games played."""
    summary = weekly_summary(session)
    return await notifier.send_weekly_summary(weekly_lines(summary), f"{summary['from']} – {summary['to']}")


async def run_weekly_summary() -> None:
    try:
        with Session(engine) as session:
            sent = await send_weekly_summary(session, deps.get_notifier())
        logger.info("Weekly summary %s", "sent" if sent else "not sent (no webhook or Discord refused)")
    except Exception:
        logger.exception("Weekly summary failed")


async def fill_missing_colors(session: Session, scryfall: ScryfallClient) -> int:
    """Fetch the colours of cards that were stored before colours were kept. Returns how many were filled in."""
    cards = list(session.exec(select(Card).where(Card.colors == None)))  # noqa: E711
    if not cards:
        return 0
    found, _ = await scryfall.get_collection([{"id": card.scryfall_id} for card in cards])
    by_id = {data.scryfall_id: data for data in found}
    filled = 0
    for card in cards:
        data = by_id.get(card.scryfall_id)
        if data is not None and data.colors is not None:
            card.colors = data.colors
            session.add(card)
            filled += 1
    session.commit()
    return filled


async def check_wishlist(
    session: Session,
    cardmarket: CardmarketScraper | None,
    notifier: DiscordNotifier,
    now: dt.datetime | None = None,
    cooldown_hours: int | None = None,
    scryfall: ScryfallClient | None = None,
) -> list[Alert]:
    """Fetch current wishlist prices and alert for every card at or below its target.

    With `scryfall`, today's Scryfall prices of the wishlist cards are fetched
    first (one bulk request per 75 cards). Cardmarket is the preferred source
    on top of that; when scraping fails, is paused or is disabled, the Scryfall
    price is used so alerts keep working.
    """
    now = now or utcnow()
    if cooldown_hours is None:
        cooldown_hours = get_settings().alert_cooldown_hours
    alerts: list[Alert] = []

    entries = session.exec(
        select(WishlistEntry, Card)
        .join(Card, WishlistEntry.card_id == Card.id)
        .where(WishlistEntry.active == True)  # noqa: E712
    ).all()
    if scryfall is not None and entries:
        try:
            found, _ = await scryfall.get_collection([{"id": card.scryfall_id} for _, card in entries])
        except ScryfallError as exc:
            logger.warning("Could not refresh Scryfall prices for the wishlist: %s", exc)
        else:
            await scryfall.fill_missing_prices(found)
            fresh = {data.scryfall_id: data for data in found}
            for _, card in entries:
                data = fresh.get(card.scryfall_id)
                if data is not None:
                    record_scryfall_price(session, card.id, data)
    scryfall_prices = latest_snapshots(session, SCRYFALL)
    for entry, card in entries:
        price, source = None, CARDMARKET
        if cardmarket is not None and entry.cardmarket_url:
            try:
                price = (await cardmarket.fetch_price(entry.cardmarket_url)).low
            except CardmarketPaused:
                pass  # already reported once, when the pause started
            except CardmarketError as exc:
                logger.warning("Cardmarket check failed for %s: %s", card.name, exc)
        if price is not None:
            record_snapshot(session, card.id, CARDMARKET, price)
        else:
            fallback = scryfall_prices.get(card.id)
            price, source = (fallback.price_eur if fallback else None), SCRYFALL
        if price is None or price > entry.target_price:
            continue
        if recently_alerted(session, entry.id, now, cooldown_hours):
            continue
        if notifier.configured and not await notifier.send_price_alert(
            card, price, entry.target_price, entry.cardmarket_url, source
        ):
            continue  # not recorded, so the next run tries again
        alert = Alert(wishlist_entry_id=entry.id, sent_at=now, price_at_alert=price)
        session.add(alert)
        alerts.append(alert)

    record_daily_total(session)
    session.commit()
    return alerts


async def run_collection_update() -> None:
    try:
        with Session(engine) as session:
            updated = await update_collection_prices(session, deps.get_scryfall())
            rises = await check_price_rises(session, deps.get_notifier())
        logger.info("Price update done: %s cards, %s price rises", updated, len(rises))
    except Exception:
        logger.exception("Price update failed")


async def run_fill_colors() -> None:
    from app.services.decks import fill_deck_colors, fill_deck_commanders

    try:
        with Session(engine) as session:
            filled = await fill_missing_colors(session, deps.get_scryfall())
            decks = await fill_deck_colors(session, deps.get_scryfall())
            await fill_deck_commanders(session, deps.get_scryfall())
            from app.services.deck_insights import fill_card_info

            await fill_card_info(session, deps.get_scryfall())  # types and mana values for the curve
        if filled or decks:
            logger.info("Colours filled in for %s cards and %s decks", filled, decks)
    except Exception:
        logger.exception("Filling in colours failed")


async def run_wishlist_check() -> None:
    try:
        with Session(engine) as session:
            alerts = await check_wishlist(
                session, deps.get_cardmarket(), deps.get_notifier(), scryfall=deps.get_scryfall()
            )
        logger.info("Wishlist check done: %s alerts", len(alerts))
    except Exception:
        logger.exception("Wishlist check failed")


async def run_deck_sync() -> None:
    """Take over changes made on Archidekt in the decks that follow a link."""
    from app.services.decks import sync_all_decks

    try:
        with Session(engine) as session:
            results = await sync_all_decks(session)
            session.commit()
        changed = [result.summary() for result in results if result.anything]
        logger.info("Deck sync done: %s decks checked, %s updated", len(results), len(changed))
        for line in changed:
            logger.info(line)
    except Exception:
        logger.exception("Deck sync failed")


def start_scheduler() -> AsyncIOScheduler:
    settings = get_settings()
    scheduler = AsyncIOScheduler()
    scheduler.add_job(run_collection_update, "cron", hour=settings.daily_update_hour, id="collection-prices")
    scheduler.add_job(run_wishlist_check, "interval", hours=settings.wishlist_check_hours, id="wishlist-check")
    if settings.deck_sync_hours > 0:
        scheduler.add_job(run_deck_sync, "interval", hours=settings.deck_sync_hours, id="deck-sync")
    if settings.weekly_summary_day in {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}:
        scheduler.add_job(
            run_weekly_summary, "cron", day_of_week=settings.weekly_summary_day,
            hour=settings.weekly_summary_hour, id="weekly-summary",
        )
    scheduler.add_job(run_fill_colors, "date", id="card-colors")  # once, right after starting
    scheduler.start()
    return scheduler
