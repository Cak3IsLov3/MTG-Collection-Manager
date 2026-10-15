"""A deck next to EDHREC: the cards other decks with the same commander play most, and which of
those you could add from your collection."""

from dataclasses import dataclass, field

from sqlmodel import Session, select

from app.edhrec import EdhrecClient, commander_slug
from app.scryfall import ScryfallClient, ScryfallError
from app.models import Card, Deck, DeckEntry
from app.services.collection import collection_rows
from app.services.decks import BASIC_LANDS, _names, deck_usage

EDHREC_PAGE = "https://edhrec.com/commanders/{slug}"
# Lists that repeat cards of the type lists; only used for cards no type list has.
_EXTRA_LISTS = ("newcards", "topcards", "highliftcards")


@dataclass
class EdhrecCard:
    name: str
    category: str  # "Creatures", "Mana Artifacts", ...
    inclusion: int  # percent of the commander's decks that play it
    synergy: int  # percent: how much more it is played with this commander than elsewhere
    in_deck: bool = False
    owned: int = 0  # copies in your collection
    free: int = 0  # of those, copies no deck uses
    card: Card | None = None  # a printing you own, for its picture and price
    price: float | None = None


@dataclass
class EdhrecComparison:
    commander: str
    decks: int  # decks on EDHREC with this commander
    url: str
    cards: list[EdhrecCard] = field(default_factory=list)  # EDHREC's cards, most played first

    @property
    def missing(self) -> list[EdhrecCard]:
        return [card for card in self.cards if not card.in_deck]

    @property
    def in_deck(self) -> int:
        return sum(card.in_deck for card in self.cards)

    @property
    def free_to_add(self) -> list[EdhrecCard]:
        return [card for card in self.missing if card.free > 0]


def _collection_by_name(session: Session) -> dict[str, list]:
    """Per name key: [copies owned, copies free, cheapest printing, its price]."""
    used: dict[int, int] = {}
    for entry_id, decks in deck_usage(session).items():
        used[entry_id] = sum(copies for _, copies in decks)
    found: dict[str, list] = {}
    for row in collection_rows(session):
        free = max(0, row.entry.quantity - used.get(row.entry.id, 0))
        for key in _names(row.card.name):
            item = found.setdefault(key, [0, 0, row.card, row.unit_price])
            item[0] += row.entry.quantity
            item[1] += free
            if row.unit_price is not None and (item[3] is None or row.unit_price < item[3]):
                item[2], item[3] = row.card, row.unit_price
    return found


def compare(session: Session, deck: Deck, page: dict) -> EdhrecComparison:
    data = (page.get("container") or {}).get("json_dict") or {}
    lists = data.get("cardlists") or []
    ordered = [cl for cl in lists if cl.get("tag") not in _EXTRA_LISTS] + [cl for cl in lists if cl.get("tag") in _EXTRA_LISTS]
    in_deck = set().union(*(_names(e.name) for e in session.exec(select(DeckEntry).where(DeckEntry.deck_id == deck.id))))
    have = _collection_by_name(session)
    commander_keys = _names(deck.commander or "")
    result = EdhrecComparison(deck.commander or "", 0, EDHREC_PAGE.format(slug=commander_slug(deck.commander or "")))
    seen: set[str] = set()
    for cardlist in ordered:
        for view in cardlist.get("cardviews") or []:
            name = view.get("name") or ""
            keys = _names(name)
            if not name or keys & seen or keys & commander_keys or name.casefold() in BASIC_LANDS:
                continue
            seen |= keys
            potential = view.get("potential_decks") or 0
            result.decks = max(result.decks, potential)
            card = EdhrecCard(
                name=name, category=cardlist.get("header") or "",
                inclusion=round((view.get("num_decks") or 0) / potential * 100) if potential else 0,
                synergy=round((view.get("synergy") or 0) * 100), in_deck=bool(keys & in_deck),
            )
            owned = next((have[key] for key in keys if key in have), None)
            if owned:
                card.owned, card.free, card.card, card.price = owned
            result.cards.append(card)
    result.cards.sort(key=lambda card: (-card.inclusion, card.name))
    return result


WEAK_BELOW = 10  # percent: a card fewer of the commander's decks play counts as a weak pick
MAX_WEAK = 15


@dataclass
class WeakCard:
    name: str
    kind: str | None  # "Creature", "Instant", ...
    inclusion: int | None  # percent; None: not among the cards EDHREC lists, so played even less
    swap: EdhrecCard | None = None  # a popular card of the same kind you have a free copy of


def weakest_cards(session: Session, deck: Deck, comparison: EdhrecComparison) -> list[WeakCard]:
    """The cards of a deck that the fewest decks with its commander play, least played first, each
    with a popular card of the same kind from your collection to swap it for, if there is one."""
    from app.services.deck_insights import card_type

    listed = {key: card for card in comparison.cards for key in _names(card.name)}
    commander = _names(deck.commander or "")
    weak = []
    for entry in session.exec(select(DeckEntry).where(DeckEntry.deck_id == deck.id).order_by(DeckEntry.name)):
        keys = _names(entry.name)
        if keys & commander or entry.name.casefold() in BASIC_LANDS:
            continue
        hit = next((listed[key] for key in keys if key in listed), None)
        if hit is None or hit.inclusion < WEAK_BELOW:
            weak.append(WeakCard(entry.name, card_type(entry.type_line), hit.inclusion if hit else None))
    weak.sort(key=lambda card: (-1 if card.inclusion is None else card.inclusion, card.name))
    weak = weak[:MAX_WEAK]
    swaps = [card for card in comparison.free_to_add if card.inclusion >= WEAK_BELOW]
    for card in weak:
        swap = next((s for s in swaps if card.kind and s.card and card_type(s.card.type_line) == card.kind), None)
        if swap is not None:
            card.swap = swap
            swaps.remove(swap)
    return weak


def lowest_listed(comparison: EdhrecComparison) -> int:
    """The inclusion of the least played card EDHREC lists: cards not listed are played less."""
    return min((card.inclusion for card in comparison.cards), default=0)


async def compare_deck(
    session: Session, deck: Deck, client: EdhrecClient, scryfall: ScryfallClient | None = None
) -> EdhrecComparison | None:
    """None when the deck has no commander (EDHREC is about commanders). With `scryfall`, the cards
    you own from EDHREC's list are looked up when their type is not known yet (for the swaps)."""
    if not deck.commander:
        return None
    comparison = compare(session, deck, await client.commander(deck.commander))
    if scryfall is not None:
        from app.services.suggestions import fill_card_roles

        try:
            await fill_card_roles(session, scryfall, [card.card for card in comparison.free_to_add if card.card])
        except ScryfallError:
            pass  # the swaps then only use cards whose type is known
    return comparison
