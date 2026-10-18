"""Application settings, read from environment variables / .env."""

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    database_url: str
    discord_webhook_url: str | None
    scheduler_enabled: bool
    daily_update_hour: int
    wishlist_check_hours: int
    deck_sync_hours: int
    alert_cooldown_hours: int
    # Discord message when a card you own rises at least this many percent (0 = off) and euros,
    # compared with its price that many days ago.
    price_rise_pct: float
    price_rise_min_eur: float
    price_rise_days: int
    # Discord message when a card on your wishlist gets at least this many percent (0 = off) and
    # euros cheaper, compared with its price that many days ago.
    price_drop_pct: float
    price_drop_min_eur: float
    price_drop_days: int
    # Weekly summary on Discord: day (mon..sun, empty = off) and hour.
    weekly_summary_day: str
    weekly_summary_hour: int
    # A Discord message when a card you own worth at least this much (0 = off) gets a new printing,
    # looking at printings released (or announced) in the last that many days.
    reprint_min_eur: float
    reprint_days: int
    # The token overlay for OBS (/stream): the file it keeps the board in (presets and the deck go
    # next to it).
    stream_state: str
    # Tokens you always bring along, so the token suggestions leave them out.
    standard_tokens: tuple[str, ...]
    # A Discord message when the automatic sync finds changes in a deck on Archidekt.
    deck_change_alerts: bool
    # Daily backup of the database: folder, how many to keep (0 = off) and the hour.
    backup_dir: str
    backup_keep: int
    backup_hour: int
    scryfall_min_interval: float
    scryfall_slow_interval: float
    cardmarket_min_interval: float
    cardmarket_enabled: bool
    user_agent: str = "MTGCollectionManager/1.0"


@lru_cache
def get_settings() -> Settings:
    return Settings(
        database_url=os.getenv("DATABASE_URL", "sqlite:///./mtg.db"),
        discord_webhook_url=os.getenv("DISCORD_WEBHOOK_URL") or None,
        scheduler_enabled=_bool("SCHEDULER_ENABLED", True),
        daily_update_hour=int(os.getenv("DAILY_UPDATE_HOUR", "6")),
        wishlist_check_hours=int(os.getenv("WISHLIST_CHECK_HOURS", "6")),
        deck_sync_hours=int(os.getenv("DECK_SYNC_HOURS", "6")),
        alert_cooldown_hours=int(os.getenv("ALERT_COOLDOWN_HOURS", "24")),
        price_rise_pct=float(os.getenv("PRICE_RISE_PCT", "25")),
        price_rise_min_eur=float(os.getenv("PRICE_RISE_MIN_EUR", "1")),
        price_rise_days=int(os.getenv("PRICE_RISE_DAYS", "7")),
        price_drop_pct=float(os.getenv("PRICE_DROP_PCT", "15")),
        price_drop_min_eur=float(os.getenv("PRICE_DROP_MIN_EUR", "0.5")),
        price_drop_days=int(os.getenv("PRICE_DROP_DAYS", "7")),
        weekly_summary_day=os.getenv("WEEKLY_SUMMARY_DAY", "mon").strip().lower(),
        weekly_summary_hour=int(os.getenv("WEEKLY_SUMMARY_HOUR", "9")),
        reprint_min_eur=float(os.getenv("REPRINT_MIN_EUR", "5")),
        reprint_days=int(os.getenv("REPRINT_DAYS", "60")),
        stream_state=os.getenv("STREAM_STATE", "stream-state.json").strip() or "stream-state.json",
        standard_tokens=tuple(
            name.strip() for name in os.getenv("STANDARD_TOKENS", "Clue,Copy").split(",") if name.strip()
        ),
        deck_change_alerts=_bool("DECK_CHANGE_ALERTS", True),
        backup_dir=os.getenv("BACKUP_DIR", "backups").strip() or "backups",
        backup_keep=int(os.getenv("BACKUP_KEEP", "14")),
        backup_hour=int(os.getenv("BACKUP_HOUR", "3")),
        scryfall_min_interval=float(os.getenv("SCRYFALL_MIN_INTERVAL", "0.1")),
        scryfall_slow_interval=float(os.getenv("SCRYFALL_SLOW_INTERVAL", "0.6")),
        cardmarket_min_interval=float(os.getenv("CARDMARKET_MIN_INTERVAL", "5")),
        cardmarket_enabled=_bool("CARDMARKET_ENABLED", True),
    )
