"""From a saved deck to the stream overlay: the tokens it makes, ready to put on the board."""

import asyncio
import datetime as dt

from sqlmodel import Session, select

from app.icons import icon
from app.models import Card, Deck, DeckEntry, Game, utcnow
from app.scryfall import name_key
from app.services.deck_insights import token_suggestions
from app.services.decks import BRACKETS, deck_tokens
from app.services.achievements import newly_unlocked, unlocked_keys
from app.services.games import deck_plays, log_game, tonight
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
        "colors": "".join(c for c in (deck.colors or "") if c in "WUBRG"),
    }


RESULT_REPEAT = dt.timedelta(minutes=2)


def record_result(result: str, deck: dict) -> dict:
    """The stream says the game is over: log it for the saved deck in the control panel, on tonight's
    date. Pressed again within two minutes (a double press), it is not logged twice."""
    from app.database import engine

    if deck.get("source") != "collection" or not deck.get("deck_id"):
        return {"logged": False, "note": "Pick one of your saved decks to log the game too"}
    with Session(engine) as session:
        saved = session.get(Deck, deck["deck_id"])
        if saved is None:
            return {"logged": False, "note": "That deck is no longer saved"}
        last = session.exec(select(Game).where(Game.deck_id == saved.id).order_by(Game.created_at.desc())).first()
        created = last.created_at.replace(tzinfo=dt.UTC) if last and last.created_at.tzinfo is None else (last.created_at if last else None)
        if created and utcnow() - created < RESULT_REPEAT and last.played_on >= tonight():
            return {"logged": False, "record": deck_intro(session, saved)["record"], "note": "Already logged a moment ago",
                    "tonight": tonight_score(session)}
        before = unlocked_keys(session)
        log_game(session, saved, result, played_on=tonight())
        session.commit()
        new = [{"icon": a.icon, "svg": str(icon(a.icon)), "title": a.title, "text": a.text}
               for a in newly_unlocked(session, before)]
        return {"logged": True, "record": deck_intro(session, saved)["record"], "tonight": tonight_score(session),
                "achievements": new}


def tonight_score(session: Session | None = None) -> dict:
    """Your wins, losses and draws of tonight's game night, for the stream."""
    if session is None:
        from app.database import engine

        with Session(engine) as own:
            return tonight_score(own)
    score = {"wins": 0, "losses": 0, "draws": 0}
    for game in session.exec(select(Game).where(Game.played_on >= tonight())):
        score[{"win": "wins", "loss": "losses", "draw": "draws"}[game.result]] += 1
    return score


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


async def deck_for_stream(session: Session, deck: Deck, scryfall=None) -> dict:
    """The deck as the control panel shows a scanned deck: every token its cards make (the ones you
    always bring too) and the tokens on its own token list, each once, with the cards that make it.
    With `scryfall`, the cards Scryfall was not asked about yet (or again) are looked up first."""
    if scryfall is not None:
        from app.scryfall import ScryfallError
        from app.services.deck_insights import fill_card_info

        try:
            await fill_card_info(session, scryfall, deck.id)
        except ScryfallError:
            session.rollback()
    suggested = token_suggestions(session, deck, include_standard=True)
    made_by = {name_key(s.name): s.made_by for s in suggested}
    ids = [s.scryfall_id for s in suggested]
    ids += [t.scryfall_id for t in deck_tokens(session, deck) if t.scryfall_id]
    ids = list(dict.fromkeys(ids))
    cards = await asyncio.to_thread(_cards_for, ids) if ids else []
    merged: dict[tuple, dict] = {}
    for card in cards:
        for info in server.token_infos(card):
            key = (info["name"], info["power"], info["toughness"], info["text"], info["part"])
            makers = made_by.get(name_key(card.get("name", ""))) or made_by.get(name_key(info["name"]), [])
            merged.setdefault(key, {**info, "made_by": makers})
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
