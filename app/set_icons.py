"""Set symbols from Scryfall, served from this app.

The pages colour a set symbol by rarity with a CSS mask, and a browser only allows that for a picture
from the same site; svgs.scryfall.io does not allow it from other sites. So the app fetches each symbol
once and keeps it in memory. Promo and other small sets use the symbol of their main set: Scryfall's
list of sets says which picture every set code uses.
"""

import time

from app.scryfall import ScryfallClient, ScryfallError

SETS_FOR = 7 * 24 * 3600  # the list of sets is looked up again after a week
_icons: dict[str, str] = {}  # set code -> picture address
_loaded_at = 0.0
_svgs: dict[str, bytes] = {}  # picture address -> the picture


async def set_icon(scryfall: ScryfallClient, code: str) -> bytes | None:
    """The symbol of a set, or None when Scryfall does not know the set (or cannot be reached)."""
    global _icons, _loaded_at
    code = code.lower()
    if not _icons or time.monotonic() - _loaded_at > SETS_FOR:
        try:
            _icons = await scryfall.set_icons()
            _loaded_at = time.monotonic()
        except ScryfallError:
            if not _icons:
                return None
    url = _icons.get(code)
    if url is None:
        return None
    if url not in _svgs:
        try:
            _svgs[url] = await scryfall.fetch_svg(url)
        except ScryfallError:
            return None
    return _svgs[url]
