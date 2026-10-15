"""Process-wide client singletons, so each external source keeps a single rate-limit queue."""

from app.cardmarket import CardmarketScraper
from app.config import get_settings
from app.discord import DiscordNotifier
from app.edhrec import EdhrecClient
from app.scryfall import ScryfallClient
from app.spellbook import SpellbookClient

_scryfall: ScryfallClient | None = None
_cardmarket: CardmarketScraper | None = None
_spellbook: SpellbookClient | None = None
_edhrec: EdhrecClient | None = None


def get_scryfall() -> ScryfallClient:
    global _scryfall
    if _scryfall is None:
        _scryfall = ScryfallClient()
    return _scryfall


def get_cardmarket() -> CardmarketScraper | None:
    """None when scraping is switched off; the wishlist then runs on Scryfall prices only."""
    global _cardmarket
    if not get_settings().cardmarket_enabled:
        return None
    if _cardmarket is None:
        _cardmarket = CardmarketScraper()
    return _cardmarket


def get_spellbook() -> SpellbookClient:
    global _spellbook
    if _spellbook is None:
        _spellbook = SpellbookClient()
    return _spellbook


def get_edhrec() -> EdhrecClient:
    global _edhrec
    if _edhrec is None:
        _edhrec = EdhrecClient()
    return _edhrec


def get_notifier() -> DiscordNotifier:
    return DiscordNotifier(get_settings().discord_webhook_url)


async def close_clients() -> None:
    global _scryfall, _cardmarket, _spellbook, _edhrec
    if _scryfall is not None:
        await _scryfall.aclose()
        _scryfall = None
    if _cardmarket is not None:
        await _cardmarket.aclose()
        _cardmarket = None
    if _spellbook is not None:
        await _spellbook.aclose()
        _spellbook = None
    if _edhrec is not None:
        await _edhrec.aclose()
        _edhrec = None
