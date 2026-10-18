"""From a saved deck to the stream overlay: the tokens it makes, ready to put on the board."""

import asyncio

from sqlmodel import Session, select

from app.models import Deck, DeckEntry
from app.scryfall import name_key
from app.services.deck_insights import token_suggestions
from app.services.decks import deck_tokens
from app.stream import server


def _cards_for(ids: list[str]) -> list[dict]:
    try:
        return server.scryfall_cards(ids)
    except server.DeckError as exc:
        raise StreamLinkError(exc.message) from exc


class StreamLinkError(Exception):
    pass


async def deck_for_stream(session: Session, deck: Deck) -> dict:
    """The deck as the control panel shows a scanned deck: every token its cards make (the ones you
    always bring too) and the tokens on its own token list, each once, with the cards that make it."""
    suggested = token_suggestions(session, deck, include_standard=True)
    made_by = {name_key(s.name): s.made_by for s in suggested}
    ids = [s.scryfall_id for s in suggested]
    ids += [t.scryfall_id for t in deck_tokens(session, deck) if t.scryfall_id]
    ids = list(dict.fromkeys(ids))
    cards = await asyncio.to_thread(_cards_for, ids) if ids else []
    merged: dict[tuple, dict] = {}
    for card in cards:
        info = server.token_info(card)
        if not info:
            continue
        key = (info["name"], info["power"], info["toughness"], info["text"])
        merged.setdefault(key, {**info, "made_by": made_by.get(name_key(info["name"]), [])})
    entries = session.exec(select(DeckEntry).where(DeckEntry.deck_id == deck.id)).all()
    return {
        "name": deck.name,
        "url": f"/decks/{deck.id}",
        "source": "collection",
        "deck_id": deck.id,
        "cards": sum(entry.quantity for entry in entries),
        "tokens": sorted(merged.values(), key=lambda t: (t["name"].lower(), t["text"])),
    }
