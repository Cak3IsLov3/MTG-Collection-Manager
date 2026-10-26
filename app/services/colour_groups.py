"""The names of the colour groups of Magic: a guild for two colours, a shard or wedge for three, a nephilim for four."""

from dataclasses import dataclass

COLOURS = "WUBRG"

# Every combination of colours, with the name it goes by. The letters are in WUBRG order.
NAMES = {
    "": "Colourless",
    "W": "Mono-white", "U": "Mono-blue", "B": "Mono-black", "R": "Mono-red", "G": "Mono-green",
    "WU": "Azorius", "UB": "Dimir", "BR": "Rakdos", "RG": "Gruul", "WG": "Selesnya",
    "WB": "Orzhov", "UR": "Izzet", "BG": "Golgari", "WR": "Boros", "UG": "Simic",
    "WUG": "Bant", "WUB": "Esper", "UBR": "Grixis", "BRG": "Jund", "WRG": "Naya",
    "WBG": "Abzan", "WUR": "Jeskai", "UBG": "Sultai", "WBR": "Mardu", "URG": "Temur",
    "WUBR": "Yore-Tiller", "UBRG": "Glint-Eye", "WBRG": "Dune-Brood", "WURG": "Ink-Treader", "WUBG": "Witch-Maw",
    "WUBRG": "Five-colour",
}
KINDS = {0: "Colourless", 1: "One colour", 2: "Two colours · guilds", 3: "Three colours · shards and wedges",
         4: "Four colours", 5: "Five colours"}


def normalise(colors: str | None) -> str | None:
    """The colours of a deck as letters in WUBRG order; None when they are not known yet."""
    if colors is None:
        return None
    return "".join(c for c in COLOURS if c in colors.upper())


def group_name(colors: str | None) -> str | None:
    """"Selesnya" for "WG"; None when the colours are not known yet."""
    key = normalise(colors)
    return None if key is None else NAMES[key]


@dataclass
class Group:
    colors: str
    name: str
    decks: int  # how many of your decks are in this group

    @property
    def missing(self) -> bool:
        return self.decks == 0


def overview(deck_colors: list[str | None]) -> list[Group]:
    """All 32 colour groups, each with the number of decks in it, ordered from colourless to five colours."""
    counts: dict[str, int] = {}
    for colors in deck_colors:
        key = normalise(colors)
        if key is not None:
            counts[key] = counts.get(key, 0) + 1
    ordered = sorted(NAMES, key=lambda key: (len(key), [COLOURS.index(c) for c in key]))
    return [Group(key, NAMES[key], counts.get(key, 0)) for key in ordered]


def by_kind(groups: list[Group]) -> list[tuple[str, list[Group]]]:
    """The groups per number of colours, for showing them in rows."""
    return [(label, [g for g in groups if len(g.colors) == size]) for size, label in KINDS.items()]
