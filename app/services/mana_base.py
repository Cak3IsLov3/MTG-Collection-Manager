"""Does the mana base carry the spells? Mana sources per colour against the coloured symbols in the
mana costs, after Frank Karsten's method: how many sources of a colour a 99-card deck needs to
cast a spell on curve most of the time, worked out with the hypergeometric distribution."""

import re
from dataclasses import dataclass, field
from math import comb

from sqlmodel import Session, select

from app.models import Deck, DeckBasicLand, DeckEntry
from app.services.deck_insights import card_type
from app.scryfall import name_key

COLOURS = "WUBRG"
NAMES = {"W": "White", "U": "Blue", "B": "Black", "R": "Red", "G": "Green"}
BASIC_COLOUR = {"plains": "W", "island": "U", "swamp": "B", "mountain": "R", "forest": "G", "wastes": "C"}
CHANCE = 0.85  # how sure you want to be to have the colours for a spell on its turn
ROCK_MAX = 2  # mana rocks and dorks up to this mana value count as a source too
FIRST_TURN = 2  # one-drops count as turn-two plays: in Commander turn one hardly decides a game
_SYMBOL = re.compile(r"\{([^}]+)\}")


def chance_at_least(deck: int, hits: int, seen: int, wanted: int) -> float:
    """The chance of `wanted` or more of `hits` cards among `seen` cards drawn from `deck` cards."""
    if wanted <= 0:
        return 1.0
    seen = min(seen, deck)
    total = comb(deck, seen)
    return sum(comb(hits, i) * comb(deck - hits, seen - i) for i in range(wanted, min(hits, seen) + 1)) / total


def seen_by(turn: int) -> int:
    """Cards you have seen on a turn: seven, plus a draw per turn (in multiplayer you are mostly on the draw)."""
    return 7 + turn


def sources_needed(pips: int, turn: int, deck: int = 99) -> int:
    """The sources of one colour a deck needs to have `pips` of them by `turn`, with CHANCE."""
    for sources in range(deck + 1):
        if chance_at_least(deck, sources, seen_by(turn), pips) >= CHANCE:
            return sources
    return deck


def cost_parts(mana_cost: str | None) -> list[tuple[str, int, dict[str, int]]]:
    """"{1}{U}{U} // {R}" -> [("{1}{U}{U}", 3, {"U": 2}), ("{R}", 1, {"R": 1})]: per face the cost, its
    mana value and its coloured symbols.
    Hybrid and Phyrexian symbols ask for no one colour, X counts as nothing."""
    parts = []
    for face in (mana_cost or "").split(" // "):
        symbols = _SYMBOL.findall(face)
        if not symbols:
            continue
        value, pips = 0, {}
        for symbol in symbols:
            if symbol.isdigit():
                value += int(symbol)
            elif symbol in COLOURS:
                value += 1
                pips[symbol] = pips.get(symbol, 0) + 1
            elif symbol not in ("X", "Y", "Z"):
                value += 1
        parts.append((face.strip(), value, pips))
    return parts


@dataclass
class ColourCheck:
    colour: str
    sources: int = 0  # lands (and early rocks or dorks) that can make it
    from_rocks: int = 0  # of those, mana rocks and dorks
    symbols: int = 0  # coloured symbols of this colour in all mana costs
    needed: int = 0  # the sources the hardest spell asks for
    hardest: str | None = None  # that spell
    hardest_cost: str | None = None

    @property
    def name(self) -> str:
        return NAMES[self.colour]

    @property
    def status(self) -> str:
        """ "ok", "close" (a few short) or "short"."""
        if self.sources >= self.needed:
            return "ok"
        return "close" if self.sources >= self.needed - 3 else "short"


@dataclass
class ManaBase:
    cards: int = 0  # in the 99 (the deck without the commander)
    lands: int = 0
    rocks: int = 0  # mana rocks and dorks up to ROCK_MAX mana value
    colours: list[ColourCheck] = field(default_factory=list)
    land_drops: list[tuple[str, int]] = field(default_factory=list)  # ("2 lands in your opening hand", 87)
    unknown: int = 0  # cards not looked up yet

    @property
    def advice(self) -> list[str]:
        lines = []
        for check in self.colours:
            if check.status != "ok":
                lines.append(
                    f"{check.name}: {check.sources} source{'s' if check.sources != 1 else ''}, about {check.needed} "
                    f"to cast {check.hardest} ({check.hardest_cost}) on turn {max(_turn(check.hardest_cost), FIRST_TURN)}"
                )
        if self.cards >= 90 and self.lands + self.rocks / 2 < 33:
            lines.append(f"{self.lands} lands and {self.rocks} early mana rocks: most Commander decks play 35 to 38 lands")
        return lines


def _turn(cost: str | None) -> int:
    return max((value for _, value, _ in cost_parts(cost)), default=0)


def mana_base(session: Session, deck: Deck) -> ManaBase:
    base = ManaBase()
    identity = [c for c in COLOURS if c in (deck.colors or "")]
    commander = name_key(deck.commander) if deck.commander else None
    entries = list(session.exec(select(DeckEntry).where(DeckEntry.deck_id == deck.id)))
    produces: dict[str, int] = {}  # colour -> sources
    rocks: dict[str, int] = {}
    spells: list[tuple[DeckEntry, str, int, dict[str, int]]] = []

    def add(colours: str, count: int, rock: bool = False) -> None:
        if "F" in colours:  # a fetch land finds any of your colours
            colours = "".join(identity)
        for colour in set(colours):
            produces[colour] = produces.get(colour, 0) + count
            if rock:
                rocks[colour] = rocks.get(colour, 0) + count

    for entry in entries:
        if entry.produces is None:
            base.unknown += entry.quantity
            continue
        kind = card_type(entry.type_line)
        if name_key(entry.name) != commander:
            base.cards += entry.quantity
        if kind == "Land":
            base.lands += entry.quantity
            add(entry.produces, entry.quantity)
            continue
        for cost, value, pips in cost_parts(entry.mana_cost):
            if pips:
                spells.append((entry, cost, value, pips))
        if entry.produces and kind in ("Artifact", "Creature") and (entry.cmc or 0) <= ROCK_MAX and name_key(entry.name) != commander:
            base.rocks += entry.quantity
            add(entry.produces, entry.quantity, rock=True)
    for land in session.exec(select(DeckBasicLand).where(DeckBasicLand.deck_id == deck.id)):
        if land.quantity:
            base.cards += land.quantity
            base.lands += land.quantity
            add(BASIC_COLOUR.get(land.name.split()[-1].casefold(), ""), land.quantity)

    deck_size = max(base.cards, 60)
    for colour in identity:
        check = ColourCheck(colour, sources=produces.get(colour, 0), from_rocks=rocks.get(colour, 0))
        for entry, cost, value, pips in spells:
            if colour not in pips:
                continue
            check.symbols += pips[colour] * entry.quantity
            needed = sources_needed(pips[colour], max(value, pips[colour], FIRST_TURN), deck_size)
            if needed > check.needed:
                check.needed, check.hardest, check.hardest_cost = needed, entry.name, cost
        base.colours.append(check)

    if base.lands:
        base.land_drops = [
            (f"{wanted} lands {when}", round(100 * chance_at_least(deck_size, base.lands, seen, wanted)))
            for wanted, seen, when in (
                (2, 7, "in your opening hand"), (3, seen_by(3), "by turn 3"), (4, seen_by(4), "by turn 4"),
                (5, seen_by(5), "by turn 5"),
            )
        ]
    return base
