"""A deck made of the cards you have: the commander and the free cards of your collection that the decks with
that commander play most (EDHREC), without lands."""

from dataclasses import dataclass, field

from sqlmodel import Session

from app.models import Deck
from app.services import edhrec_compare
from app.services.decks import _names

MAX_CARDS = 62  # a deck of 99 cards plays about 37 lands, which are left to you
MIN_CARDS = 5  # fewer than this is no deck


@dataclass
class AutoDeck:
    commander: str
    cards: list[str] = field(default_factory=list)  # the most played first
    skipped_used: int = 0  # cards of EDHREC's list that you own but that sit in other decks

    @property
    def decklist(self) -> str:
        """The deck as a list the deck pages understand: the commander and one line per card."""
        return "\n".join([f"1 {self.commander} [Commander]", *(f"1 {name}" for name in self.cards)])


def is_land(card: "edhrec_compare.EdhrecCard") -> bool:
    kind = (card.card.type_line if card.card and card.card.type_line else "")
    return "land" in card.category.lower() or "Land" in kind.split(" // ")[0]


def build(session: Session, commander: str, page: dict) -> AutoDeck | None:
    """The deck for `commander` (EDHREC's `page` of it) from your free cards; None when you own no free copy
    of the commander. Lands are left out; the cards are the ones most of its decks play."""
    comparison = edhrec_compare.compare(session, Deck(id=-1, name="", commander=commander), page)
    have = edhrec_compare._collection_by_name(session)
    if not any(have.get(key, [0, 0])[1] > 0 for key in _names(commander)):
        return None
    deck = AutoDeck(commander)
    for card in comparison.cards:  # most played first
        if is_land(card):
            continue
        if card.owned and card.free <= 0:
            deck.skipped_used += 1
        if card.free > 0 and len(deck.cards) < MAX_CARDS:
            deck.cards.append(card.name)
    return deck
