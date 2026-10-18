"""What is in a deck: mana curve, card types and roles, the bracket check, token pictures, proxies
and a text export."""

import datetime as dt
import json
import re
from dataclasses import dataclass, field

from sqlmodel import Session, or_, select

from app.models import Deck, DeckBasicLand, DeckEntry, DeckToken
from app.scryfall import ScryfallClient, ScryfallCard, name_key
from app.services.decks import DeckRow, _names, deck_tokens

# A card counts as its first type in this list ("Artifact Creature" is a creature).
TYPES = ["Creature", "Planeswalker", "Battle", "Instant", "Sorcery", "Artifact", "Enchantment", "Land"]
ROLES = {"ramp": "Ramp", "draw": "Card draw", "removal": "Removal", "wipe": "Board wipe"}
# Tokens, emblems ("Elspeth, Sun's Champion Emblem") and helper cards such as Morph and Manifest
# (a "Creature" in the shape of a token) are extras on Scryfall.
TOKEN_QUERY = "(t:token or t:emblem or layout:token or layout:emblem or layout:double_faced_token)"

_RAMP = re.compile(r"add \{|add (one|two|three) mana|search your library for (a|up to \w+) .*lands?\b.*onto the battlefield", re.I)
_DRAW = re.compile(r"\bdraws? (a|two|three|four|x|that many) cards?\b|\bdraw cards\b", re.I)
_REMOVAL = re.compile(
    r"(destroy|exile) target|counter target spell|return target (creature|nonland permanent|permanent|artifact|enchantment)"
    r"[^.]* to its owner's hand|deals \w+ damage to (any target|target creature|target planeswalker)", re.I,
)
_WIPE = re.compile(r"(destroy|exile) all (creatures|nonland permanents|artifacts|enchantments)|deals \w+ damage to each creature", re.I)
# For the Commander brackets: mass land denial (Armageddon, Ruination, Winter Orb, ...) and extra turns.
_LAND_DENIAL = re.compile(
    r"(destroy|exile|return) all (\w+ )?lands|destroy all [^.]*\band lands\b|sacrifices? (all|half|\w+) [^.]*\blands\b"
    r"|lands (don't|do not) untap|lands? (tapped for mana|a player controls) (doesn't|don't|do not) untap", re.I,
)
_EXTRA_TURN = re.compile(r"takes? (an|one|two|\w+) extra turns?", re.I)
# How many tokens a card makes at once ("create two ..."), and whether it does so again and again.
_CREATE = re.compile(r"\bcreates? (a|an|one|two|three|four|five|six|seven|eight|nine|ten|x|that many|\d+)\b", re.I)
_NUMBERS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
            "eight": 8, "nine": 9, "ten": 10, "x": 3, "that many": 3}
_REPEATS = re.compile(r"\b(whenever|at the beginning of|landfall)\b|\{t\}[^.]*create", re.I)
MAX_TOKENS_PER_CARD = 10


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


def bracket_flags(oracle_text: str) -> str:
    """What in the rules text matters for the Commander brackets: "mld" (mass land denial), "extraturn"."""
    flags = []
    if _LAND_DENIAL.search(oracle_text):
        flags.append("mld")
    if _EXTRA_TURN.search(oracle_text):
        flags.append("extraturn")
    return ",".join(flags)


def tokens_needed(oracle_text: str) -> int:
    """About how many tokens of a kind you need for one card: what it makes at once ("create two" is
    2, X counts as 3), three times over when it makes them again and again (whenever ..., at the
    beginning of ..., a tap ability)."""
    found = [_NUMBERS.get(word.lower()) or int(word) for word in (m.group(1) for m in _CREATE.finditer(oracle_text))
             if word.lower() in _NUMBERS or word.isdigit()]
    once = max(found, default=1)
    return min(MAX_TOKENS_PER_CARD, once * 3 if _REPEATS.search(oracle_text) else once)


def _token_parts(data: ScryfallCard) -> str:
    count = tokens_needed(data.oracle_text)
    # One emblem is enough: a second one of the same does nothing new.
    return json.dumps([
        {"id": token_id, "name": name, "count": 1 if name.endswith(" Emblem") else count}
        for token_id, name in data.token_parts
    ])


def _store(entry: DeckEntry, data: ScryfallCard | None) -> None:
    if data is None:
        entry.type_line, entry.roles, entry.bracket_flags = "", "", ""  # looked up, not found: not again
        entry.token_parts = "[]"
        return
    entry.token_parts = _token_parts(data)
    entry.type_line = data.type_line
    entry.cmc = data.cmc
    entry.roles = card_roles(data.type_line, data.oracle_text)
    entry.bracket_flags = bracket_flags(data.oracle_text)
    entry.image_url = data.image_url


async def fill_card_info(session: Session, scryfall: ScryfallClient, deck_id: int | None = None) -> int:
    """Look up type, mana value, rules text and picture of deck cards that do not have them yet
    (of one deck, or of all), and the pictures of their tokens. Returns the number of cards filled in."""
    statement = select(DeckEntry).where(or_(
        DeckEntry.type_line == None, DeckEntry.bracket_flags == None, DeckEntry.token_parts == None,  # noqa: E711
    ))
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


_land_images: dict[str, str] = {}


async def basic_land_images(scryfall: ScryfallClient) -> dict[str, str]:
    """A picture of every basic land ("Plains" -> image url), looked up once and then kept."""
    from app.services.decks import BASIC_LAND_NAMES

    if not _land_images:
        found, _ = await scryfall.get_collection([{"name": name} for name in BASIC_LAND_NAMES])
        _land_images.update({data.name: data.image_url for data in found if data.image_url})
    return _land_images


_game_changers: dict = {"day": None, "names": frozenset()}


async def game_changers(scryfall: ScryfallClient) -> frozenset[str]:
    """The names (name keys, every face too) of the cards on the Game Changers list. Wizards changes
    the list now and then, so it is fetched from Scryfall once a day."""
    today = dt.date.today()
    if _game_changers["day"] != today:
        results = await scryfall.search("is:gamechanger", unique="cards")
        names: set[str] = set()
        for data in results:
            names |= _names(data.get("name", ""))
        _game_changers.update(day=today, names=frozenset(names))
    return _game_changers["names"]


@dataclass
class BracketCheck:
    """What a deck has that the Commander brackets limit: Game Changers (none in brackets 1 and 2, up
    to three in bracket 3), mass land denial (not before bracket 4), extra turns (not to be chained
    before bracket 4) and two-card infinite combos (none in brackets 1 and 2, not early in the game in
    bracket 3)."""

    bracket: int | None  # the bracket you set
    game_changers: list[str] = field(default_factory=list)
    land_denial: list[str] = field(default_factory=list)
    extra_turns: list[str] = field(default_factory=list)
    two_card_combos: list[str] = field(default_factory=list)  # "Thassa's Oracle + Demonic Consultation"
    known: bool = True  # False: the Game Changers list could not be fetched
    combos_checked: bool = True  # False: Commander Spellbook was not asked (yet)
    unchecked: int = 0  # cards Scryfall has not told about yet

    @property
    def minimum(self) -> int:
        """The lowest bracket the deck fits in (1 means 1 or 2: they differ in intent, not in cards)."""
        if len(self.game_changers) > 3 or self.land_denial:
            return 4
        return 3 if self.game_changers or self.two_card_combos else 1

    @property
    def problems(self) -> list[str]:
        """Why the deck does not fit the bracket you set; empty when it does (or none is set)."""
        if self.bracket is None or self.bracket >= 4:
            return []
        found = []
        count = len(self.game_changers)
        if self.bracket <= 2 and count:
            found.append(f"{count} Game Changer{'s' if count != 1 else ''}, while bracket {self.bracket} has none")
        elif self.bracket == 3 and count > 3:
            found.append(f"{count} Game Changers, while bracket 3 allows three")
        if self.land_denial:
            found.append(f"mass land denial ({', '.join(self.land_denial)}), which only bracket 4 and up allow")
        if self.bracket <= 2 and self.two_card_combos:
            count = len(self.two_card_combos)
            found.append(f"{count} two-card combo{'s' if count != 1 else ''} ({'; '.join(self.two_card_combos)}), "
                         f"while bracket {self.bracket} has none")
        return found

    @property
    def fits(self) -> bool:
        return not self.problems


def bracket_check(session: Session, deck: Deck, changers: frozenset[str] | None, combos=None) -> BracketCheck:
    """Check a deck against the bracket rules; `changers` None: the Game Changers list is not known.
    `combos` are the deck's combos from Commander Spellbook (services.combos.DeckCombos); without
    them the combos found the last time are used."""
    from app.services.combos import deck_combos

    combos = combos if combos is not None else deck_combos(deck)
    check = BracketCheck(
        bracket=deck.bracket, known=changers is not None, combos_checked=combos.checked,
        two_card_combos=[combo.label() for combo in combos.two_card],
    )
    for entry in session.exec(select(DeckEntry).where(DeckEntry.deck_id == deck.id).order_by(DeckEntry.name)):
        if changers and _names(entry.name) & changers:
            check.game_changers.append(entry.name)
        if entry.bracket_flags is None:
            check.unchecked += 1
            continue
        flags = entry.bracket_flags.split(",")
        if "mld" in flags:
            check.land_denial.append(entry.name)
        if "extraturn" in flags:
            check.extra_turns.append(entry.name)
    return check


def bracket_warnings(session: Session, decks: list[Deck], changers: frozenset[str] | None) -> dict[int, list[str]]:
    """Per deck that does not fit the bracket you set: why not."""
    warnings = {}
    for deck in decks:
        if deck.bracket is not None and deck.bracket < 4:
            problems = bracket_check(session, deck, changers).problems
            if problems:
                warnings[deck.id] = problems
    return warnings


@dataclass
class TokenSuggestion:
    """A token the cards of a deck make, and about how many of it you need."""

    scryfall_id: str
    name: str
    needed: int
    made_by: list[str] = field(default_factory=list)
    have: DeckToken | None = None  # the same token in the deck's token list
    image_url: str | None = None

    @property
    def status(self) -> str:
        """"missing", "short" (fewer than needed in the list) or "ok"."""
        if self.have is None:
            return "missing"
        return "short" if self.have.quantity < self.needed else "ok"


MAX_TOKENS = 20
_token_images: dict[str, str] = {}


def token_suggestions(session: Session, deck: Deck, include_standard: bool = False) -> list[TokenSuggestion]:
    """The tokens the cards of a deck make (from Scryfall), each with about how many you need, next
    to the deck's own token list. Missing ones first, then the ones you have too few of. The tokens
    you always bring (STANDARD_TOKENS) are left out, unless `include_standard`."""
    from app.config import get_settings

    standard = set() if include_standard else {name_key(name) for name in get_settings().standard_tokens}
    # Per token name: cards point at different prints of the same token (many Food tokens).
    found: dict[str, TokenSuggestion] = {}
    for entry in session.exec(select(DeckEntry).where(DeckEntry.deck_id == deck.id).order_by(DeckEntry.name)):
        for part in json.loads(entry.token_parts or "[]"):
            if name_key(part["name"]) in standard:
                continue
            suggestion = found.setdefault(name_key(part["name"]), TokenSuggestion(part["id"], part["name"], 0))
            suggestion.needed = min(MAX_TOKENS, suggestion.needed + part["count"] * entry.quantity)
            if entry.name not in suggestion.made_by:
                suggestion.made_by.append(entry.name)
    listed = deck_tokens(session, deck)
    for suggestion in found.values():
        same_name = [t for t in listed if name_key(t.name) == name_key(suggestion.name)]
        suggestion.have = max(same_name, key=lambda t: t.quantity, default=None)
        if suggestion.have and suggestion.have.scryfall_id and suggestion.have.image_url:
            # The version you chose for your list.
            suggestion.scryfall_id, suggestion.image_url = suggestion.have.scryfall_id, suggestion.have.image_url
        else:
            suggestion.image_url = _token_images.get(suggestion.scryfall_id) or None
    order = {"missing": 0, "short": 1, "ok": 2}
    return sorted(found.values(), key=lambda s: (order[s.status], name_key(s.name)))


async def fill_token_images(scryfall: ScryfallClient, suggestions: list[TokenSuggestion]) -> None:
    """Pictures of the suggested tokens (looked up once, then kept)."""
    todo = [s.scryfall_id for s in suggestions if not s.image_url and s.scryfall_id not in _token_images]
    if todo:
        found, _ = await scryfall.get_collection([{"id": token_id} for token_id in todo])
        for data in found:
            _token_images[data.scryfall_id] = data.image_url or ""
    for suggestion in suggestions:
        suggestion.image_url = suggestion.image_url or _token_images.get(suggestion.scryfall_id) or None


def apply_token_suggestions(session: Session, deck: Deck, scryfall_id: str | None = None) -> int:
    """Put suggested tokens in the deck's token list: missing ones are added with the number needed,
    and ones with fewer than needed are raised to it. Only `scryfall_id` when given. Returns how many
    tokens changed."""
    from app.services.decks import _count_named_tokens

    changed = 0
    for suggestion in token_suggestions(session, deck):
        if scryfall_id and suggestion.scryfall_id != scryfall_id or suggestion.status == "ok":
            continue
        token = suggestion.have or DeckToken(
            deck_id=deck.id, name=suggestion.name, scryfall_id=suggestion.scryfall_id,
            image_url=_token_images.get(suggestion.scryfall_id) or None,
        )
        token.quantity = max(token.quantity if suggestion.have else 0, suggestion.needed)
        session.add(token)
        changed += 1
    if changed:
        session.flush()
        _count_named_tokens(session, deck)
        if deck.uses_tokens is False:
            deck.uses_tokens = None  # it has tokens now
            session.add(deck)
        session.flush()
    return changed


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
