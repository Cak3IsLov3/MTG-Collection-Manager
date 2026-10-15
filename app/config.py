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
    # Weekly summary on Discord: day (mon..sun, empty = off) and hour.
    weekly_summary_day: str
    weekly_summary_hour: int
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
        weekly_summary_day=os.getenv("WEEKLY_SUMMARY_DAY", "mon").strip().lower(),
        weekly_summary_hour=int(os.getenv("WEEKLY_SUMMARY_HOUR", "9")),
        scryfall_min_interval=float(os.getenv("SCRYFALL_MIN_INTERVAL", "0.1")),
        scryfall_slow_interval=float(os.getenv("SCRYFALL_SLOW_INTERVAL", "0.6")),
        cardmarket_min_interval=float(os.getenv("CARDMARKET_MIN_INTERVAL", "5")),
        cardmarket_enabled=_bool("CARDMARKET_ENABLED", True),
    )
