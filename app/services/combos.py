"""The combos in a deck, found by Commander Spellbook. The result is kept with the deck and only asked
again when its card list changes."""

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass, field

from sqlmodel import Session, select

from app.models import Card, CollectionEntry, Deck, DeckEntry
from app.scryfall import name_key
from app.services.decks import _names
from app.spellbook import SpellbookClient, SpellbookError

logger = logging.getLogger(__name__)

COMBO_URL = "https://commanderspellbook.com/combo/{id}/"
MAX_INCLUDED = 40
MAX_ALMOST = 15


@dataclass
class Combo:
    cards: list[str]
    produces: list[str]  # "Infinite mana", "Win the game", ...
    id: str = ""
    mana_value: int | None = None  # the mana value of everything you need to cast
    missing: list[str] = field(default_factory=list)  # cards the deck does not have (one card away)

    @property
    def url(self) -> str | None:
        return COMBO_URL.format(id=self.id) if self.id else None

    @property
    def infinite(self) -> bool:
        """Goes on forever or wins: what the brackets mean by an infinite combo."""
        return any(effect.startswith("Infinite") or effect.startswith("Win the game") for effect in self.produces)

    @property
    def two_card(self) -> bool:
        return len(self.cards) == 2 and self.infinite

    def label(self) -> str:
        return " + ".join(self.cards)


@dataclass
class DeckCombos:
    included: list[Combo] = field(default_factory=list)
    almost: list[Combo] = field(default_factory=list)  # one card away
    checked: bool = False  # False: not asked yet, or Commander Spellbook could not be reached

    @property
    def two_card(self) -> list[Combo]:
        return [combo for combo in self.included if combo.two_card]


def _deck_cards(session: Session, deck: Deck) -> tuple[list[str], list[str]]:
    """(cards, commanders) of a deck, by name."""
    names = sorted({entry.name for entry in session.exec(select(DeckEntry).where(DeckEntry.deck_id == deck.id))})
    commanders = [name for name in names if deck.commander and name_key(name) == name_key(deck.commander)]
    return [name for name in names if name not in commanders], commanders


def combo_key(session: Session, deck: Deck) -> str:
    cards, commanders = _deck_cards(session, deck)
    return hashlib.sha1(json.dumps([cards, commanders]).encode("utf-8")).hexdigest()


def _combo(data: dict, have: set[str]) -> Combo:
    cards = [use["card"]["name"] for use in data.get("uses", []) if use.get("card", {}).get("name")]
    produces = [item["feature"]["name"] for item in data.get("produces", []) if item.get("feature", {}).get("name")]
    return Combo(
        cards=cards, produces=produces, id=str(data.get("id") or ""), mana_value=data.get("manaValueNeeded"),
        missing=[name for name in cards if not _names(name) & have],
    )


def _simplify(result: dict, have: set[str]) -> dict:
    """Only what the app shows, so the stored result stays small."""

    def popular(items: list[dict]) -> list[dict]:
        return sorted(items, key=lambda data: -(data.get("popularity") or 0))

    included = [_combo(data, have) for data in popular(result.get("included") or [])][:MAX_INCLUDED]
    almost = [
        combo for combo in (_combo(data, have) for data in popular(result.get("almostIncluded") or []))
        if len(combo.missing) == 1
    ][:MAX_ALMOST]
    keep = ("cards", "produces", "id", "mana_value", "missing")
    return {
        "included": [{key: getattr(combo, key) for key in keep} for combo in included],
        "almost": [{key: getattr(combo, key) for key in keep} for combo in almost],
    }


def deck_combos(deck: Deck) -> DeckCombos:
    """The combos found the last time (without asking Commander Spellbook)."""
    if not deck.combos:
        return DeckCombos()
    data = json.loads(deck.combos)
    return DeckCombos(
        included=[Combo(**item) for item in data.get("included", [])],
        almost=[Combo(**item) for item in data.get("almost", [])],
        checked=True,
    )


def is_current(session: Session, deck: Deck) -> bool:
    return bool(deck.combos) and deck.combos_key == combo_key(session, deck)


async def check_deck_combos(session: Session, deck: Deck, client: SpellbookClient) -> DeckCombos:
    """Ask Commander Spellbook for the combos of a deck, unless its cards did not change since the last time."""
    key = combo_key(session, deck)
    if deck.combos and deck.combos_key == key:
        return deck_combos(deck)
    cards, commanders = _deck_cards(session, deck)
    if not cards and not commanders:
        return DeckCombos(checked=True)
    result = await client.find_combos(cards, commanders)
    have = set().union(*(_names(name) for name in cards + commanders))
    deck.combos = json.dumps(_simplify(result, have))
    deck.combos_key = key
    session.add(deck)
    session.flush()
    return deck_combos(deck)


async def check_all_decks(session: Session, client: SpellbookClient) -> int:
    """Check the decks whose cards changed since their last check; returns how many were checked."""
    checked = 0
    for deck in session.exec(select(Deck).order_by(Deck.id)).all():
        if is_current(session, deck):
            continue
        try:
            await check_deck_combos(session, deck, client)
        except SpellbookError as exc:
            logger.warning("Could not check the combos of %s: %s", deck.name, exc)
            break  # try again next time, not once per deck
        session.commit()
        checked += 1
        await asyncio.sleep(0)
    return checked


def owned_missing(session: Session, combos: DeckCombos) -> set[str]:
    """The cards the deck is one card away from that you have in your collection."""
    have = owned_names(session)
    return {name for combo in combos.almost for name in combo.missing if _names(name) & have}


def owned_names(session: Session) -> set[str]:
    """Name keys (every face too) of the cards in the collection."""
    names: set[str] = set()
    for name in session.exec(select(Card.name).join(CollectionEntry, CollectionEntry.card_id == Card.id)).all():
        names |= _names(name)
    return names
