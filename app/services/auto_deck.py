"""A deck made of the cards you have: the commander, the free cards of your collection that the decks with that
commander play most (EDHREC), the lands you own that those decks play, and basic lands for the rest."""

from dataclasses import dataclass, field

from sqlmodel import Session

from app.models import Deck
from app.services import edhrec_compare
from app.services.decks import BASIC_LANDS, _names
from app.scryfall import name_key

MAX_CARDS = 62  # spells: with the commander and the lands a deck of 100
LAND_COUNT = 37  # lands in total, the ones you own first
MIN_CARDS = 5  # fewer spells than this is no deck
BASIC_FOR_COLOUR = {"W": "Plains", "U": "Island", "B": "Swamp", "R": "Mountain", "G": "Forest"}


@dataclass
class AutoDeck:
    commander: str
    cards: list[str] = field(default_factory=list)  # the spells, the most played first
    skipped_used: int = 0  # cards of EDHREC's list that you own but that sit in other decks
    lands: list[str] = field(default_factory=list)  # lands (not basic) you own that these decks play
    basics: dict[str, int] = field(default_factory=dict)  # basic lands to fill the rest

    @property
    def decklist(self) -> str:
        """The deck as a list the deck pages understand: the commander, the cards, the lands and the basics."""
        return "\n".join([
            f"1 {self.commander} [Commander]",
            *(f"1 {name}" for name in self.cards),
            *(f"1 {name}" for name in self.lands),
            *(f"{count} {land}" for land, count in self.basics.items()),
        ])


def is_land(card: "edhrec_compare.EdhrecCard") -> bool:
    kind = (card.card.type_line if card.card and card.card.type_line else "")
    return "land" in card.category.lower() or "Land" in kind.split(" // ")[0]


def basic_lands(identity: str, spells: list[str], colours_of: dict[str, str], count: int) -> dict[str, int]:
    """`count` basic lands for a commander of this colour identity ("WUB", "" for colourless), shared out over its
    colours by how many of the spells use each (every colour gets at least one)."""
    if count <= 0:
        return {}
    letters = [letter for letter in "WUBRG" if letter in identity]
    if not letters:
        return {"Wastes": count}
    weights = {letter: 1 + sum(1 for name in spells if letter in colours_of.get(name, "")) for letter in letters}
    total = sum(weights.values())
    shares = {letter: count * weight // total for letter, weight in weights.items()}
    # What rounding left over goes to the colours with the largest remainder.
    for letter in sorted(letters, key=lambda l: (count * weights[l] % total), reverse=True)[: count - sum(shares.values())]:
        shares[letter] += 1
    return {BASIC_FOR_COLOUR[letter]: number for letter, number in shares.items() if number}


def build(session: Session, commander: str, page: dict) -> AutoDeck | None:
    """The deck for `commander` (EDHREC's `page` of it) from your free cards; None when you own no free copy
    of the commander. The spells are the ones most of its decks play, then come the lands of that list that you own,
    and basic lands in its colours fill the rest."""
    comparison = edhrec_compare.compare(session, Deck(id=-1, name="", commander=commander), page)
    have = edhrec_compare._collection_by_name(session)
    owned = [have[key] for key in _names(commander) if key in have and have[key][1] > 0]
    if not owned:
        return None
    deck = AutoDeck(commander)
    colours: dict[str, str] = {}
    for card in comparison.cards:  # most played first
        if card.card is not None and card.card.colors:
            colours[card.name] = card.card.colors
        if is_land(card):
            if card.free > 0 and name_key(card.name) not in BASIC_LANDS and len(deck.lands) < LAND_COUNT:
                deck.lands.append(card.name)
            continue
        if card.owned and card.free <= 0:
            deck.skipped_used += 1
        if card.free > 0 and len(deck.cards) < MAX_CARDS:
            deck.cards.append(card.name)
    identity = getattr(owned[0][2], "identity", None) or ""
    deck.basics = basic_lands(identity, deck.cards, colours, LAND_COUNT - len(deck.lands))
    return deck
