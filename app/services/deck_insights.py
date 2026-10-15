"""What is in a deck: mana curve, card types and roles, token pictures, proxies and a text export."""

import re
from dataclasses import dataclass, field

from sqlmodel import Session, select

from app.models import Deck, DeckBasicLand, DeckEntry, DeckToken
from app.scryfall import ScryfallClient, ScryfallCard, name_key
from app.services.decks import DeckRow, _names, deck_tokens

# A card counts as its first type in this list ("Artifact Creature" is a creature).
TYPES = ["Creature", "Planeswalker", "Battle", "Instant", "Sorcery", "Artifact", "Enchantment", "Land"]
ROLES = {"ramp": "Ramp", "draw": "Card draw", "removal": "Removal", "wipe": "Board wipe"}
# Tokens and emblems ("Elspeth, Sun's Champion Emblem") are extras on Scryfall.
TOKEN_QUERY = "(t:token or t:emblem)"

_RAMP = re.compile(r"add \{|add (one|two|three) mana|search your library for (a|up to \w+) .*lands?\b.*onto the battlefield", re.I)
_DRAW = re.compile(r"\bdraws? (a|two|three|four|x|that many) cards?\b|\bdraw cards\b", re.I)
_REMOVAL = re.compile(
    r"(destroy|exile) target|counter target spell|return target (creature|nonland permanent|permanent|artifact|enchantment)"
    r"[^.]* to its owner's hand|deals \w+ damage to (any target|target creature|target planeswalker)", re.I,
)
_WIPE = re.compile(r"(destroy|exile) all (creatures|nonland permanents|artifacts|enchantments)|deals \w+ damage to each creature", re.I)


def card_type(type_line: str | None) -> str | None:
    """The type a card counts as, from the front face: "Instant // Land" is an instant."""
    front = (type_line or "").split(" // ")[0]
    return next((kind for kind in TYPES if kind in front), None)


def card_roles(type_line: str, oracle_text: str) -> str:
    """What a card does, guessed from its rules text: "ramp,draw" etc. Lands are not counted as ramp."""
    roles = []
    if card_type(type_line) != "Land" and _RAMP.search(oracle_text):
        roles.append("ramp")
    if _DRAW.search(oracle_text):
        roles.append("draw")
    if _WIPE.search(oracle_text):
        roles.append("wipe")
    elif _REMOVAL.search(oracle_text):
        roles.append("removal")
    return ",".join(roles)


def _store(entry: DeckEntry, data: ScryfallCard | None) -> None:
    if data is None:
        entry.type_line, entry.roles = "", ""  # looked up, not found: not again
        return
    entry.type_line = data.type_line
    entry.cmc = data.cmc
    entry.roles = card_roles(data.type_line, data.oracle_text)
    entry.image_url = data.image_url


async def fill_card_info(session: Session, scryfall: ScryfallClient, deck_id: int | None = None) -> int:
    """Look up type, mana value, rules text and picture of deck cards that do not have them yet
    (of one deck, or of all), and the pictures of their tokens. Returns the number of cards filled in."""
    statement = select(DeckEntry).where(DeckEntry.type_line == None)  # noqa: E711
    if deck_id is not None:
        statement = statement.where(DeckEntry.deck_id == deck_id)
    entries = list(session.exec(statement))
    token_statement = select(DeckToken).where(DeckToken.image_url == None)  # noqa: E711
    if deck_id is not None:
        token_statement = token_statement.where(DeckToken.deck_id == deck_id)
    tokens = list(session.exec(token_statement))
    if not entries and not tokens:
        return 0

    # By id when the deck list names the printing, else by name (the front face of a double-faced card).
    identifiers = {}
    for entry in entries:
        key = entry.scryfall_id or name_key(entry.name)
        identifiers[key] = {"id": entry.scryfall_id} if entry.scryfall_id else {"name": entry.name.split(" // ")[0]}
    for token in tokens:
        if token.scryfall_id:
            identifiers[token.scryfall_id] = {"id": token.scryfall_id}
    found, _ = await scryfall.get_collection(list(identifiers.values())) if identifiers else ([], [])
    by_id = {data.scryfall_id: data for data in found}
    by_name: dict[str, ScryfallCard] = {}
    for data in found:
        for key in _names(data.name):
            by_name.setdefault(key, data)

    for entry in entries:
        data = by_id.get(entry.scryfall_id) if entry.scryfall_id else None
        data = data or next((by_name[key] for key in _names(entry.name) if key in by_name), None)
        _store(entry, data)
        session.add(entry)
    for token in tokens:
        data = by_id.get(token.scryfall_id) if token.scryfall_id else None
        if data is None and '"' not in token.name:
            results = await scryfall.search(f'!"{token.name}" {TOKEN_QUERY}', unique="cards", include_extras="true")
            # A plain token of that name, not one face of a double-faced token.
            exact = [r for r in results if name_key(r.get("name", "")) == name_key(token.name)] or results
            data = ScryfallCard.from_json(exact[0]) if exact else None
        token.image_url = (data.image_url if data else None) or ""
        if data is not None and name_key(data.name) == name_key(token.name):
            token.name = data.name  # Scryfall's spelling: "cat beast" -> "Cat Beast"
        session.add(token)
    session.commit()
    return len(entries)


@dataclass
class TokenVersion:
    """One of the tokens with a name: there are many Angels (4/4 flying, 1/1 lifelink, ...)."""

    scryfall_id: str
    name: str
    description: str  # "4/4 · Flying" or the rules text
    type_line: str
    image_url: str | None


def _describe(data: dict) -> str:
    faces = data.get("card_faces") or [data]
    face = faces[0]
    parts = []
    if face.get("power") is not None:
        parts.append(f"{face['power']}/{face.get('toughness', '?')}")
    colors = "".join(face.get("colors") or data.get("colors") or []) or "colourless"
    parts.append(colors)
    text = (face.get("oracle_text") or "").replace("\n", " ").strip()
    if text:
        parts.append(text if len(text) <= 90 else text[:87] + "…")
    return " · ".join(parts)


async def token_versions(scryfall: ScryfallClient, name: str) -> list[TokenVersion]:
    """The different tokens with this name (one print of each), to choose from."""
    if '"' in name or not name.strip():
        return []
    results = await scryfall.search(
        f'!"{name.strip()}" {TOKEN_QUERY}', unique="cards", order="released", dir="desc", include_extras="true"
    )
    exact = [r for r in results if name_key(r.get("name", "")) == name_key(name)] or results
    versions = []
    for data in exact:
        card = ScryfallCard.from_json(data)
        versions.append(TokenVersion(card.scryfall_id, card.name, _describe(data), card.type_line, card.image_url))
    return versions


async def choose_token_version(session: Session, scryfall: ScryfallClient, deck: Deck, token_id: int, scryfall_id: str) -> DeckToken:
    """Use this exact token (by its Scryfall id) for a token of the deck."""
    token = session.get(DeckToken, token_id)
    if token is None or token.deck_id != deck.id:
        raise ValueError("That token is not in this deck")
    card = await scryfall.get_by_id(scryfall_id)
    token.scryfall_id, token.image_url = card.scryfall_id, card.image_url or ""
    if name_key(card.name) == name_key(token.name):
        token.name = card.name
    session.add(token)
    session.flush()
    return token


@dataclass
class DeckStats:
    """Mana curve, card types and roles of a deck, counted per copy."""

    curve: dict[str, int] = field(default_factory=dict)  # "0" .. "6", "7+" -> cards (lands not counted)
    types: dict[str, int] = field(default_factory=dict)  # "Creature" -> cards, basic lands included
    roles: dict[str, int] = field(default_factory=dict)  # "Ramp" -> cards
    average_cmc: float | None = None
    unknown: int = 0  # cards Scryfall did not know (yet)

    @property
    def has_data(self) -> bool:
        return bool(self.types)


def deck_stats(session: Session, deck: Deck) -> DeckStats:
    stats = DeckStats(curve={str(n): 0 for n in range(7)} | {"7+": 0})
    spells, total_cmc = 0, 0.0
    for entry in session.exec(select(DeckEntry).where(DeckEntry.deck_id == deck.id)):
        kind = card_type(entry.type_line)
        if kind is None:
            stats.unknown += entry.quantity
            continue
        stats.types[kind] = stats.types.get(kind, 0) + entry.quantity
        for role in filter(None, (entry.roles or "").split(",")):
            stats.roles[ROLES[role]] = stats.roles.get(ROLES[role], 0) + entry.quantity
        if kind != "Land":
            cmc = int(entry.cmc or 0)
            stats.curve["7+" if cmc >= 7 else str(cmc)] += entry.quantity
            spells += entry.quantity
            total_cmc += (entry.cmc or 0) * entry.quantity
    lands = sum(row.quantity for row in session.exec(select(DeckBasicLand).where(DeckBasicLand.deck_id == deck.id)))
    if lands:
        stats.types["Land"] = stats.types.get("Land", 0) + lands
    stats.types = {kind: stats.types[kind] for kind in TYPES if stats.types.get(kind)}
    stats.roles = {label: stats.roles[label] for label in ROLES.values() if stats.roles.get(label)}
    stats.average_cmc = round(total_cmc / spells, 2) if spells else None
    return stats


def export_text(session: Session, deck: Deck, rows: list[DeckRow]) -> str:
    """The deck as a text list Archidekt and Moxfield can import: "1 Sol Ring (cmm) 410", the
    commander first, then the other cards, the basic lands and the tokens."""
    commander = name_key(deck.commander) if deck.commander else None

    def line(row: DeckRow) -> str:
        printing = f" ({row.set_code.upper()}) {row.collector_number or ''}".rstrip() if row.set_code else ""
        return f"{row.needed} {row.name}{printing}{' *F*' if row.foil else ''}"

    first = [line(row) for row in rows if name_key(row.name) == commander]
    rest = [line(row) for row in rows if name_key(row.name) != commander]
    lands = [
        f"{row.quantity} {row.name}"
        for row in session.exec(select(DeckBasicLand).where(DeckBasicLand.deck_id == deck.id).order_by(DeckBasicLand.name))
        if row.quantity
    ]
    tokens = [f"{token.quantity} {token.name} Token" for token in deck_tokens(session, deck)]
    parts = [f"// {deck.name}"]
    if first:
        parts += ["// Commander", *first]
    parts += ["// Deck", *rest, *lands]
    if tokens:
        parts += ["// Tokens (not part of the deck)", *tokens]
    return "\n".join(parts) + "\n"


def proxies(session: Session, rows: list[DeckRow], everything: bool = False) -> list[tuple[str, str]]:
    """(name, picture) for every copy to print: the missing cards, or with `everything` the whole deck."""
    pictures = {
        entry.id: entry.image_url
        for entry in session.exec(select(DeckEntry).where(DeckEntry.id.in_([row.entry_id for row in rows if row.entry_id])))
    }
    sheet = []
    for row in rows:
        copies = row.needed if everything else row.missing
        sheet += [(row.name, pictures.get(row.entry_id) or "")] * max(0, copies)
    return sheet
