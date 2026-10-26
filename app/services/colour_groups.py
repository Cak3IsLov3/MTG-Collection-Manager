"""The names of the colour groups of Magic: a guild for two colours, a shard or wedge for three, a nephilim for four."""

import datetime as dt
from dataclasses import dataclass

from sqlmodel import Session

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


def order(colors: str | None) -> int | str:
    """The place of these colours in the list of all groups (colourless first); "" when not known yet."""
    key = normalise(colors)
    if key is None:
        return ""
    return sorted(NAMES, key=lambda k: (len(k), [COLOURS.index(c) for c in k])).index(key)


def by_kind(groups: list[Group]) -> list[tuple[str, list[Group]]]:
    """The groups per number of colours, for showing them in rows."""
    return [(label, [g for g in groups if len(g.colors) == size]) for size, label in KINDS.items()]


def group_key(slug: str) -> str | None:
    """The colours of an address like "wg", "GW" or "c" (colourless); None when it is no colour group."""
    key = "" if slug.strip().lower() == "c" else normalise(slug)
    return key if key in NAMES and (key or slug.strip().lower() == "c") and set(slug.upper()) <= set(COLOURS + "C") else None


def slug(colors: str) -> str:
    """The address of a colour group: its letters, "c" for colourless."""
    return normalise(colors).lower() or "c"


def commander_query(colors: str) -> str:
    """The Scryfall search for the commanders that have exactly these colours."""
    return f"is:commander legal:commander id={normalise(colors).lower() or 'c'}"


def _image(raw: dict) -> str | None:
    uris = raw.get("image_uris") or ((raw.get("card_faces") or [{}])[0].get("image_uris")) or {}
    return uris.get("normal")


_COMMANDERS: dict[tuple[str, dt.date], list[dict]] = {}  # per group and day: Scryfall answers the same all day


async def all_commanders(scryfall, colors: str) -> list[dict]:
    """Every commander with exactly these colours, the most played first (Scryfall's EDHREC order), without duplicates."""
    key = (normalise(colors), dt.date.today())
    if key in _COMMANDERS:
        return _COMMANDERS[key]
    found, seen = [], set()
    for raw in await scryfall.search(commander_query(colors), order="edhrec", unique="cards"):
        name = raw.get("name") or ""
        if not name or name in seen:
            continue
        seen.add(name)
        found.append({"name": name, "image_url": _image(raw), "type_line": raw.get("type_line") or "",
                      "scryfall_uri": raw.get("scryfall_uri"), "rank": raw.get("edhrec_rank")})
    if len(_COMMANDERS) > 64:
        _COMMANDERS.clear()
    _COMMANDERS[key] = found
    return found


def owned_commanders(session: Session, commanders: list[dict]) -> list[dict]:
    """The commanders of this group that you have a copy of that no deck uses, the most played first, each
    with the number of copies and how many of them are free. A commander whose copies all sit in decks is left out."""
    from app.services.decks import _names
    from app.services.edhrec_compare import _collection_by_name

    have = _collection_by_name(session)  # per name key: [copies owned, copies free, a printing, its price]
    found = []
    for commander in commanders:
        owned = [have[key] for key in _names(commander["name"]) if key in have]
        copies = max((item[0] for item in owned), default=0)
        free = max((item[1] for item in owned), default=0)
        if free > 0:
            found.append({**commander, "copies": copies, "free": free})
    return found


@dataclass
class Pool:
    unique: int  # different cards of your collection that fit the colours
    copies: int


def fitting_pool(session: Session, colors: str) -> Pool:
    """What your collection holds that could be played in a deck of these colours (the colours of the cards
    themselves; cards whose colours are not known yet are left out)."""
    from app.services.collection import collection_rows

    allowed = set(normalise(colors) or "")
    unique, copies = set(), 0
    for row in collection_rows(session):
        have = row.card.colors
        if have is not None and set(have) <= allowed:
            unique.add(row.card.name)
            copies += row.entry.quantity
    return Pool(len(unique), copies)


def commander_coverage(session: Session, name: str, page: dict, top: int = 60) -> dict:
    """How many of the `top` cards EDHREC's decks with this commander play most you own."""
    from app.models import Deck
    from app.services import edhrec_compare

    comparison = edhrec_compare.compare(session, Deck(id=-1, name="", commander=name), page)
    cards = comparison.cards[:top]
    owned = sum(1 for card in cards if card.owned)
    return {"commander": name, "owned": owned, "of": len(cards), "percent": round(owned / len(cards) * 100) if cards else 0,
            "decks": comparison.decks, "url": comparison.url}
