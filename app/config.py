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
    alert_cooldown_hours: int
    scryfall_min_interval: float
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
        alert_cooldown_hours=int(os.getenv("ALERT_COOLDOWN_HOURS", "24")),
        scryfall_min_interval=float(os.getenv("SCRYFALL_MIN_INTERVAL", "0.1")),
        cardmarket_min_interval=float(os.getenv("CARDMARKET_MIN_INTERVAL", "5")),
        cardmarket_enabled=_bool("CARDMARKET_ENABLED", True),
    )
