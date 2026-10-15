"""Cards from the collection that could go in a deck: in its colours, not in the deck yet, and with a
copy that no deck uses. Grouped by what the deck is short of."""

from dataclasses import dataclass, field

from sqlmodel import Session, select

from app.models import Card, Deck, DeckEntry
from app.scryfall import ScryfallClient
from app.services.collection import collection_rows
from app.services.deck_insights import ROLES, card_roles, card_type
from app.services.decks import BASIC_LANDS, _names, deck_usage

# About how many of each a Commander deck runs; the deck is short when it has fewer.
ROLE_TARGETS = {"ramp": 10, "draw": 10, "removal": 8, "wipe": 3}
PER_GROUP = 8


@dataclass
class Suggestion:
    card: Card  # the cheapest printing with a free copy
    free: int  # free copies, all printings together
    price: float | None
    roles: list[str] = field(default_factory=list)  # "ramp", "draw", ...

    @property
    def kind(self) -> str | None:
        return card_type(self.card.type_line)


@dataclass
class SuggestionGroup:
    key: str  # "ramp", "draw", "removal", "wipe", "land", "other"
    title: str
    cards: list[Suggestion]
    have: int | None = None  # for a role: how many the deck has
    target: int | None = None  # and about how many it should have

    @property
    def short(self) -> int:
        return max(0, (self.target or 0) - (self.have or 0))


@dataclass
class DeckSuggestions:
    groups: list[SuggestionGroup] = field(default_factory=list)
    candidates: int = 0  # cards that fit the deck
    pending: int = 0  # cards Scryfall has not told about yet
    no_colours: bool = False  # the deck's colours are not known yet


def _fits(card_colors: str | None, deck_colors: str) -> bool:
    return card_colors is not None and set(card_colors) <= set(deck_colors)


async def fill_card_roles(session: Session, scryfall: ScryfallClient, cards: list[Card]) -> int:
    """Look up type, mana value and rules text of collection cards that do not have them yet."""
    todo = [card for card in cards if card.type_line is None]
    if not todo:
        return 0
    found, _ = await scryfall.get_collection([{"id": card.scryfall_id} for card in todo])
    by_id = {data.scryfall_id: data for data in found}
    for card in todo:
        data = by_id.get(card.scryfall_id)
        card.type_line = data.type_line if data else ""
        card.cmc = data.cmc if data else None
        card.roles = card_roles(data.type_line, data.oracle_text) if data else ""
        session.add(card)
    session.commit()
    return len(todo)


def _candidates(session: Session, deck: Deck) -> dict[str, Suggestion]:
    """Per card name: a card in the deck's colours with a free copy that is not in the deck yet."""
    in_deck = set().union(*(_names(e.name) for e in session.exec(select(DeckEntry).where(DeckEntry.deck_id == deck.id))))
    used: dict[int, int] = {}
    for entry_id, decks in deck_usage(session).items():
        used[entry_id] = sum(copies for _, copies in decks)
    found: dict[str, Suggestion] = {}
    for row in collection_rows(session):
        card = row.card
        free = row.entry.quantity - used.get(row.entry.id, 0)
        if free <= 0 or card.name.casefold() in BASIC_LANDS or _names(card.name) & in_deck:
            continue
        if not _fits(card.colors, deck.colors or "") or (card.type_line or "").startswith("Token"):
            continue
        current = found.get(card.name)
        if current is None:
            found[card.name] = Suggestion(card, free, row.unit_price)
            continue
        current.free += free
        if row.unit_price is not None and (current.price is None or row.unit_price < current.price):
            current.card, current.price = card, row.unit_price
    return found


def deck_role_counts(session: Session, deck: Deck) -> dict[str, int]:
    counts = {role: 0 for role in ROLES}
    for entry in session.exec(select(DeckEntry).where(DeckEntry.deck_id == deck.id)):
        for role in filter(None, (entry.roles or "").split(",")):
            counts[role] = counts.get(role, 0) + entry.quantity
    return counts


async def suggest(session: Session, scryfall: ScryfallClient | None, deck: Deck) -> DeckSuggestions:
    """Suggestions for a deck; with `scryfall`, cards that were never looked up are looked up first."""
    if deck.colors is None:
        return DeckSuggestions(no_colours=True)
    found = _candidates(session, deck)
    if scryfall is not None:
        await fill_card_roles(session, scryfall, [suggestion.card for suggestion in found.values()])
    result = DeckSuggestions(candidates=len(found))
    pool = []
    for suggestion in found.values():
        if suggestion.card.type_line is None:
            result.pending += 1
            continue
        if suggestion.card.type_line.startswith("Token"):
            continue
        suggestion.roles = [role for role in (suggestion.card.roles or "").split(",") if role]
        pool.append(suggestion)

    def best(cards: list[Suggestion]) -> list[Suggestion]:
        # The priciest cards first: a rough sign of how good a card is.
        return sorted(cards, key=lambda s: (-(s.price or 0), s.card.name))[:PER_GROUP]

    counts = deck_role_counts(session, deck)
    roles = []
    for role, target in ROLE_TARGETS.items():
        cards = best([s for s in pool if role in s.roles])
        if cards:
            roles.append(SuggestionGroup(role, ROLES[role], cards, counts.get(role, 0), target))
    # The roles the deck is shortest of first.
    result.groups = sorted(roles, key=lambda group: -group.short)
    lands = best([s for s in pool if s.kind == "Land"])
    if lands:
        result.groups.append(SuggestionGroup("land", "Lands", lands))
    other = best([s for s in pool if not s.roles and s.kind != "Land"])
    if other:
        result.groups.append(SuggestionGroup("other", "Other cards in these colours", other))
    return result
