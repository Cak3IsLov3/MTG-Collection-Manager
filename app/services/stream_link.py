"""From a saved deck to the stream overlay: the tokens it makes, ready to put on the board."""

import asyncio

from sqlmodel import Session, select

from app.models import Card, Deck, DeckEntry
from app.scryfall import name_key
from app.services.deck_insights import token_suggestions
from app.services.decks import BRACKETS, deck_tokens
from app.services.games import deck_plays
from app.stream import server


def _cards_for(ids: list[str]) -> list[dict]:
    try:
        return server.scryfall_cards(ids)
    except server.DeckError as exc:
        raise StreamLinkError(exc.message) from exc


class StreamLinkError(Exception):
    pass


def deck_intro(session: Session, deck: Deck) -> dict:
    """What the deck intro on stream shows: commander, deck name, bracket and how the deck has done."""
    stats = deck_plays(session).get(deck.id)
    record = None
    if stats and stats.played:
        record = f"{stats.wins} W · {stats.losses} L" + (f" · {stats.draws} D" if stats.draws else "")
    image = deck.commander_image if (deck.commander_image or "").startswith(server.ALLOWED_IMAGE_PREFIX) else None
    return {
        "name": deck.name,
        "commander": deck.commander or None,
        "image_url": image,
        "bracket": f"Bracket {deck.bracket} · {BRACKETS[deck.bracket]}" if deck.bracket in BRACKETS else None,
        "record": record,
    }


def spotlight_cards(session: Session, entries: list[DeckEntry]) -> list[dict]:
    """The cards of the deck with their picture (the printing in the deck), for the spotlight. A card
    without a known picture is still listed; the control panel then asks Scryfall for one."""
    ids = [entry.scryfall_id for entry in entries if entry.scryfall_id]
    images = {card.scryfall_id: card.image_url
              for card in session.exec(select(Card).where(Card.scryfall_id.in_(ids)))} if ids else {}
    cards: dict[str, dict] = {}
    for entry in entries:
        image = images.get(entry.scryfall_id) or entry.image_url
        if not (image or "").startswith(server.ALLOWED_IMAGE_PREFIX):
            image = None
        card = cards.setdefault(name_key(entry.name), {"name": entry.name, "image_url": image})
        card["image_url"] = card["image_url"] or image
    return sorted(cards.values(), key=lambda c: c["name"].lower())


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
        "intro": deck_intro(session, deck),
        "spotlight": spotlight_cards(session, entries),
    }
