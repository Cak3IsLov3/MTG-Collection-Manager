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
from app.models import Alert, Card, CollectionEntry, WishlistEntry, utcnow
from app.scryfall import ScryfallClient, ScryfallError
from app.services.pricing import CARDMARKET, SCRYFALL, latest_snapshots, record_scryfall_price, record_snapshot
from app.services.stats import record_daily_total
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
        updated += 1
    record_daily_total(session)
    session.commit()
    return updated


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
        logger.info("Price update done: %s cards", updated)
    except Exception:
        logger.exception("Price update failed")


async def run_wishlist_check() -> None:
    try:
        with Session(engine) as session:
            alerts = await check_wishlist(
                session, deps.get_cardmarket(), deps.get_notifier(), scryfall=deps.get_scryfall()
            )
        logger.info("Wishlist check done: %s alerts", len(alerts))
    except Exception:
        logger.exception("Wishlist check failed")


def start_scheduler() -> AsyncIOScheduler:
    settings = get_settings()
    scheduler = AsyncIOScheduler()
    scheduler.add_job(run_collection_update, "cron", hour=settings.daily_update_hour, id="collection-prices")
    scheduler.add_job(run_wishlist_check, "interval", hours=settings.wishlist_check_hours, id="wishlist-check")
    scheduler.start()
    return scheduler
