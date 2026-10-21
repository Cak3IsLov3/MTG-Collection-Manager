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
from app.models import Alert, Card, CollectionEntry, PriceDropAlert, PriceRiseAlert, WishlistEntry, utcnow
from app.scryfall import ScryfallClient, ScryfallError
from app.services.pricing import CARDMARKET, SCRYFALL, latest_snapshots, record_scryfall_price, record_snapshot
from app.services.stats import find_price_drops, find_price_rises, record_daily_total, weekly_lines, weekly_summary
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
        if data.identity is not None:
            card.identity = data.identity
        if data.rarity:
            card.rarity = data.rarity
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


async def check_price_drops(
    session: Session,
    notifier: DiscordNotifier,
    now: dt.datetime | None = None,
    min_pct: float | None = None,
    min_eur: float | None = None,
    days: int | None = None,
) -> list[PriceDropAlert]:
    """After the daily price update: tell Discord which wishlist cards got cheaper, a card once per
    period of `days` days. Separate from the alert for reaching your target price."""
    settings = get_settings()
    min_pct = settings.price_drop_pct if min_pct is None else min_pct
    min_eur = settings.price_drop_min_eur if min_eur is None else min_eur
    days = settings.price_drop_days if days is None else days
    if min_pct <= 0:
        return []
    now = now or utcnow()
    recent = {
        alert.wishlist_entry_id
        for alert in session.exec(select(PriceDropAlert).where(PriceDropAlert.sent_at >= now - dt.timedelta(days=days)))
    }
    drops = [(row, drop) for row, drop in find_price_drops(session, days, min_pct, min_eur) if row.entry.id not in recent]
    if not drops:
        return []
    alerts, lines = [], []
    for row, drop in drops:
        alerts.append(PriceDropAlert(
            wishlist_entry_id=row.entry.id, card_id=row.card.id, sent_at=now, old_price=drop.old_price,
            new_price=drop.new_price, days=days,
        ))
        target = row.entry.target_price
        lines.append(
            f"**{row.card.name}** · {row.card.set_code.upper()} · €{drop.old_price:.2f} → €{drop.new_price:.2f} "
            f"(−{abs(drop.change_pct):.0f}%) · your target €{target:.2f}{' ✓' if drop.new_price <= target else ''}"
        )
    notified = notifier.configured and await notifier.send_price_drops(lines, days)
    for alert in alerts:
        alert.notified = bool(notified)
        session.add(alert)
    session.commit()
    return alerts


def deck_change_message(session: Session, result) -> dict | None:
    """What to tell about a deck the sync changed: cards added and removed, and which new cards you
    do not have a copy for."""
    from app.services.decks import deck_url, saved_deck_rows

    if result.error or not (result.added or result.removed or result.changed):
        return None
    added = set(result.added)
    missing = [row for row in saved_deck_rows(session, result.deck) if row.name in added and row.missing > 0]
    lines = []

    def names(items: list[str]) -> str:
        return ", ".join(items[:12]) + (f" and {len(items) - 12} more" if len(items) > 12 else "")

    if result.added:
        lines.append(f"**Added** {names(result.added)}")
    if result.removed:
        lines.append(f"**Removed** {names(result.removed)}")
    if result.changed:
        lines.append(f"**Changed** {names(result.changed)}")
    if missing:
        lines.append(f"**You still need** {names([f'{row.missing}× {row.name}' for row in missing])}")
    elif result.added:
        lines.append("You have all the new cards.")
    return {"name": result.deck.name, "url": deck_url(result.deck), "lines": lines}


async def notify_deck_changes(session: Session, results: list, notifier: DiscordNotifier) -> int:
    """Tell Discord about the decks that changed on Archidekt; returns how many decks were mentioned."""
    if not get_settings().deck_change_alerts or not notifier.configured:
        return 0
    changes = [message for result in results if (message := deck_change_message(session, result))]
    if changes and await notifier.send_deck_changes(changes):
        return len(changes)
    return 0


async def check_reprints(
    session: Session, scryfall: ScryfallClient, notifier: DiscordNotifier,
    min_eur: float | None = None, days: int | None = None,
) -> list:
    """New printings of valuable cards you own; each is told once."""
    from app.services.reprints import find_reprints, record_reprints, reprint_lines

    settings = get_settings()
    min_eur = settings.reprint_min_eur if min_eur is None else min_eur
    days = settings.reprint_days if days is None else days
    if min_eur <= 0:
        return []
    reprints = await find_reprints(session, scryfall, days, min_eur)
    if not reprints:
        return []
    notified = notifier.configured and await notifier.send_reprints(reprint_lines(reprints))
    alerts = record_reprints(session, reprints, bool(notified))
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
    # Also the rarity and the colour identity, which are kept since later still.
    cards = list(session.exec(select(Card).where(
        (Card.colors == None) | (Card.rarity == None) | (Card.identity == None))))  # noqa: E711
    if not cards:
        return 0
    found, _ = await scryfall.get_collection([{"id": card.scryfall_id} for card in cards])
    by_id = {data.scryfall_id: data for data in found}
    filled = 0
    for card in cards:
        data = by_id.get(card.scryfall_id)
        if data is None:
            card.rarity = card.rarity or ""  # not looked up again
            card.colors = card.colors if card.colors is not None else ""
            card.identity = card.identity if card.identity is not None else ""
            session.add(card)
            continue
        if data.colors is not None:
            card.colors = data.colors
        card.identity = data.identity if data.identity is not None else (card.identity or "")
        card.rarity = data.rarity or ""
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
            drops = await check_price_drops(session, deps.get_notifier())
            try:
                reprints = await check_reprints(session, deps.get_scryfall(), deps.get_notifier())
                if reprints:
                    logger.info("%s reprints of cards you own", len(reprints))
            except ScryfallError as exc:
                logger.warning("Could not look for reprints: %s", exc)
        logger.info("Price update done: %s cards, %s price rises, %s wishlist price drops", updated, len(rises), len(drops))
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
            await notify_deck_changes(session, results, deps.get_notifier())
        changed = [result.summary() for result in results if result.anything]
        logger.info("Deck sync done: %s decks checked, %s updated", len(results), len(changed))
        for line in changed:
            logger.info(line)
        if changed:
            await run_combo_checks()  # the combos of the changed decks
    except Exception:
        logger.exception("Deck sync failed")


async def run_combo_checks() -> None:
    """Ask Commander Spellbook for the combos of the decks whose cards changed."""
    from app.services.combos import check_all_decks

    try:
        with Session(engine) as session:
            checked = await check_all_decks(session, deps.get_spellbook())
        if checked:
            logger.info("Combos checked for %s decks", checked)
    except Exception:
        logger.exception("Combo check failed")


async def send_game_night(session: Session, notifier: DiscordNotifier, night: dt.date) -> bool:
    """Post the recap of one game night and remember that it went out."""
    from app.services.games import game_nights, mark_recap_sent

    found = next((n for n in game_nights(session) if n.night == night), None)
    if found is None or not notifier.configured:
        return False
    if not await notifier.send_game_night(found.lines(), night.isoformat()):
        return False
    mark_recap_sent(session, night)
    session.commit()
    return True


async def run_game_night_recap() -> None:
    """In the morning: the recap of yesterday's games, if there were any and it was not sent yet."""
    from app.services.games import game_nights

    try:
        yesterday = dt.date.today() - dt.timedelta(days=1)
        with Session(engine) as session:
            night = next((n for n in game_nights(session) if n.night == yesterday), None)
            if night is not None and not night.sent:
                await send_game_night(session, deps.get_notifier(), yesterday)
    except Exception:
        logger.exception("Game night recap failed")


def run_backup() -> None:
    from app.backup import make_backup

    try:
        make_backup()
    except Exception:
        logger.exception("Database backup failed")


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
    scheduler.add_job(run_combo_checks, "date", run_date=dt.datetime.now() + dt.timedelta(minutes=2), id="combos")
    if settings.game_night_recap:
        scheduler.add_job(run_game_night_recap, "cron", hour=settings.backup_hour, minute=30, id="game-night")
    if settings.backup_keep > 0:
        from app.backup import has_backup_today

        scheduler.add_job(run_backup, "cron", hour=settings.backup_hour, id="backup")
        if not has_backup_today():
            scheduler.add_job(run_backup, "date", id="backup-now")  # the PC was off at backup time
    scheduler.start()
    return scheduler
