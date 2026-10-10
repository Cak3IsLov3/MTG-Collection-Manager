import asyncio
import random
import time


class RateLimiter:
    """Serialises callers so requests are at least `min_interval` seconds apart.

    Each external source gets its own instance, so Scryfall and Cardmarket
    are throttled independently. `jitter` adds a random extra delay to make
    scraping look less mechanical.
    """

    def __init__(self, min_interval: float, jitter: float = 0.0) -> None:
        self.min_interval = min_interval
        self.jitter = jitter
        self._next_allowed = 0.0
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        async with self._lock:
            delay = self._next_allowed - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            extra = random.uniform(0, self.jitter) if self.jitter else 0.0
            self._next_allowed = time.monotonic() + self.min_interval + extra
