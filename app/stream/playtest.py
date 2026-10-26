"""A playtest table for the stream: a deck you shuffle, draw from and play on a battlefield that the
overlay shows on your virtual camera, while your hand stays on your own screen.

The overlay only ever gets what the others may see: the battlefield, graveyard, exile and command zone,
and how many cards are in your hand and library. The hand and the order of the library are only given
out by `private()`, to the playtest page.
"""

import copy
import functools
import json
import os
import random
import tempfile
import uuid
from pathlib import Path

from pydantic import BaseModel, Field, field_validator

HAND_SIZE = 7
MAX_CARDS = 400
MAX_COUNTERS = 99
MAX_LOG = 200
MAX_UNDO = 60
ZONES = ("hand", "battlefield", "graveyard", "exile", "command")
# Where a card can be sent to: a zone, or a place in the library. "library" goes with a position (1 is the top,
# the library without the moved card is counted), "library-shuffle" puts it in and shuffles.
LIBRARY_PLACES = ("library-top", "library-bottom", "library", "library-shuffle")
DESTINATIONS = (*ZONES, *LIBRARY_PLACES)
START_LIFE = 40
DICE = {"d4": 4, "d6": 6, "d8": 8, "d10": 10, "d12": 12, "d20": 20, "d100": 100, "coin": 2}
MANA = ("W", "U", "B", "R", "G", "C")
PLAYER_COUNTERS = ("poison", "energy", "experience", "storm", "tax")  # tax: the commander tax, 2 a time


def card_row(type_line: str | None) -> str:
    """The row a card is shown in on the battlefield: "land", "creature" or "other"."""
    front = (type_line or "").split(" // ")[0]
    if "Land" in front:
        return "land"
    if "Creature" in front:
        return "creature"
    return "other"


class PlayCardIn(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    image_url: str = Field(max_length=500)
    type_line: str = Field(default="", max_length=200)
    count: int = Field(default=1, ge=1, le=100)

    @field_validator("image_url")
    @classmethod
    def check_image_url(cls, v: str) -> str:
        from app.stream.server import ALLOWED_IMAGE_PREFIX  # not at the top: server imports this module

        if not v.startswith(ALLOWED_IMAGE_PREFIX):
            raise ValueError("only Scryfall images are allowed")
        return v


class PlayDeckIn(BaseModel):
    name: str = Field(default="Deck", min_length=1, max_length=120)
    cards: list[PlayCardIn] = Field(max_length=MAX_CARDS)
    commander: PlayCardIn | None = None


class MoveIn(BaseModel):
    uid: str
    to: str
    position: int | None = Field(default=None, ge=1, le=MAX_CARDS)

    @field_validator("to")
    @classmethod
    def check_to(cls, v: str) -> str:
        if v not in DESTINATIONS:
            raise ValueError("unknown place")
        return v


class MoveAllIn(BaseModel):
    zone: str  # a zone, or "library"
    to: str

    @field_validator("zone")
    @classmethod
    def check_zone(cls, v: str) -> str:
        if v not in (*ZONES, "library"):
            raise ValueError("unknown zone")
        return v

    @field_validator("to")
    @classmethod
    def check_to(cls, v: str) -> str:
        if v not in (*ZONES, "library-top", "library-bottom", "library-shuffle"):
            raise ValueError("unknown place")
        return v


class TutorIn(BaseModel):
    uid: str
    to: str = "hand"  # where the card goes; the library is shuffled afterwards

    @field_validator("to")
    @classmethod
    def check_to(cls, v: str) -> str:
        if v not in ZONES:
            raise ValueError("unknown zone")
        return v


class MillIn(BaseModel):
    count: int = Field(default=1, ge=1, le=MAX_CARDS)
    to: str = "graveyard"
    bottom: bool = False  # from the bottom of the library instead of the top

    @field_validator("to")
    @classmethod
    def check_to(cls, v: str) -> str:
        if v not in ZONES:
            raise ValueError("unknown zone")
        return v


class CounterDelta(BaseModel):
    type: str = Field(min_length=1, max_length=24)
    delta: int = Field(ge=-MAX_COUNTERS, le=MAX_COUNTERS)


class CardPatch(BaseModel):
    tapped: bool | None = None
    counter: CounterDelta | None = None  # a counter of a kind: +1/+1, lifelink, charge...
    clear_counters: bool = False
    facedown: bool | None = None  # face down (morph): the others see a card back
    phased: bool | None = None  # dimmed (phased out)
    rot: bool | None = None  # turned 180 degrees
    power_delta: int | None = Field(default=None, ge=-MAX_COUNTERS, le=MAX_COUNTERS)
    toughness_delta: int | None = Field(default=None, ge=-MAX_COUNTERS, le=MAX_COUNTERS)


class CopyIn(BaseModel):
    count: int = Field(default=1, ge=1, le=9)


class LifePatch(BaseModel):
    life_delta: int | None = Field(default=None, ge=-200, le=200)
    life: int | None = Field(default=None, ge=-999, le=9999)
    poison_delta: int | None = Field(default=None, ge=-20, le=20)
    energy_delta: int | None = Field(default=None, ge=-99, le=99)
    experience_delta: int | None = Field(default=None, ge=-99, le=99)
    storm_delta: int | None = Field(default=None, ge=-99, le=99)
    tax_delta: int | None = Field(default=None, ge=-99, le=99)


class ManaPatch(BaseModel):
    color: str
    delta: int | None = Field(default=None, ge=-99, le=99)
    clear: bool = False  # empty the whole mana pool

    @field_validator("color")
    @classmethod
    def check_color(cls, v: str) -> str:
        if v not in (*MANA, "all"):
            raise ValueError("unknown colour")
        return v


class OrderIn(BaseModel):
    by: str = "name"

    @field_validator("by")
    @classmethod
    def check_by(cls, v: str) -> str:
        if v not in ("name", "type"):
            raise ValueError("order by name or type")
        return v


class DiceIn(BaseModel):
    kind: str = "d6"

    @field_validator("kind")
    @classmethod
    def check_kind(cls, v: str) -> str:
        if v not in DICE:
            raise ValueError("unknown die")
        return v


class DrawIn(BaseModel):
    count: int = Field(default=1, ge=1, le=HAND_SIZE * 2)


def undoable(fn):
    """Remember the table before a change, so Ctrl + Z can bring it back. A change inside a change counts once."""

    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        outer = self._depth == 0
        if outer:
            snapshot = copy.deepcopy(self._dump())
        self._depth += 1
        try:
            result = fn(self, *args, **kwargs)
        except BaseException:
            self._depth -= 1
            raise
        self._depth -= 1
        if outer:
            self._undo.append(snapshot)
            del self._undo[:-MAX_UNDO]
            self._redo.clear()
        return result

    return wrapper


class PlayStore:
    """The deck and the zones of one playtest game, kept in a file so a restart does not lose the game."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.rev = 0  # goes up with every change: the page fetches the hand when it changes
        self._depth = 0
        self._undo: list[dict] = []
        self._redo: list[dict] = []
        self._empty()
        self._load()

    def _empty(self) -> None:
        self.deck_name = ""
        self.template: list[dict] = []  # the cards of the deck, one entry per copy
        self.commander: dict | None = None
        self.library: list[dict] = []
        self.zones: dict[str, list[dict]] = {zone: [] for zone in ZONES}
        self.life = START_LIFE
        self.counts = {name: 0 for name in PLAYER_COUNTERS}
        self.mana = {color: 0 for color in MANA}
        self.turn = 1
        self.mulligans = 0
        self.opening = True  # the opening hand is still being chosen
        self.revealed: dict | None = None  # a card of the library that the others may see too
        self.roll: dict | None = None  # the last die or coin, shown on the overlay
        self.log: list[str] = []  # what you did, only for you

    def _log(self, text: str) -> None:
        self.log.append(text)
        del self.log[:-MAX_LOG]

    # ---- file
    def _dump(self) -> dict:
        return {
            "deck_name": self.deck_name, "template": self.template, "commander": self.commander,
            "library": self.library, "zones": self.zones, "life": self.life, "counts": self.counts, "mana": self.mana,
            "turn": self.turn, "mulligans": self.mulligans, "opening": self.opening, "revealed": self.revealed,
            "roll": self.roll,
        }

    def _apply(self, raw: dict) -> None:
        self.deck_name = str(raw.get("deck_name") or "")
        self.template = [c for c in raw.get("template") or [] if isinstance(c, dict)]
        self.commander = raw.get("commander") if isinstance(raw.get("commander"), dict) else None
        self.library = [c for c in raw.get("library") or [] if isinstance(c, dict)]
        for zone in ZONES:
            self.zones[zone] = [c for c in (raw.get("zones") or {}).get(zone) or [] if isinstance(c, dict)]
        self.life = int(raw.get("life", START_LIFE))
        counts = raw.get("counts") if isinstance(raw.get("counts"), dict) else {}
        self.counts = {name: int(counts.get(name, 0)) for name in PLAYER_COUNTERS}
        mana = raw.get("mana") if isinstance(raw.get("mana"), dict) else {}
        self.mana = {color: int(mana.get(color, 0)) for color in MANA}
        self.turn = int(raw.get("turn", 1))
        self.mulligans = int(raw.get("mulligans", 0))
        self.opening = bool(raw.get("opening", False))
        self.revealed = raw.get("revealed") if isinstance(raw.get("revealed"), dict) else None
        self.roll = raw.get("roll") if isinstance(raw.get("roll"), dict) else None

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        try:
            self._apply(raw)
            self.log = [str(x) for x in raw.get("log") or []][-MAX_LOG:]
            self.rev = int(raw.get("rev", 0))
        except (TypeError, ValueError, AttributeError):
            self._empty()  # a broken file: start with an empty table

    def _save(self) -> None:
        self.rev += 1
        data = json.dumps({**self._dump(), "log": self.log, "rev": self.rev}, ensure_ascii=False)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".play-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(data)
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    # ---- the deck
    @staticmethod
    def _card(template: dict) -> dict:
        return {"uid": uuid.uuid4().hex[:8], "name": template["name"], "image_url": template["image_url"],
                "type": card_row(template.get("type_line")), "tapped": False, "counters": {}, "facedown": False,
                "phased": False, "rot": False, "dp": 0, "dt": 0, "token": False}

    @staticmethod
    def _reset(card: dict) -> None:
        """A card that changes zone loses what it had on the battlefield."""
        card.update({"tapped": False, "counters": {}, "facedown": False, "phased": False, "rot": False, "dp": 0, "dt": 0})

    @property
    def loaded(self) -> bool:
        return bool(self.template or self.commander)

    @undoable
    def load(self, deck: PlayDeckIn) -> None:
        """Take a deck and start a game with it."""
        self.deck_name = deck.name
        self.template = [
            {"name": c.name, "image_url": c.image_url, "type_line": c.type_line}
            for c in deck.cards for _ in range(c.count)
        ][:MAX_CARDS]
        self.commander = (
            {"name": deck.commander.name, "image_url": deck.commander.image_url, "type_line": deck.commander.type_line}
            if deck.commander else None
        )
        self.new_game(save=False)
        self._log(f"Loaded {deck.name}")
        self._save()

    @undoable
    def new_game(self, save: bool = True) -> None:
        """Everything back in the library, shuffled, the commander in the command zone, seven cards drawn."""
        self.library = [self._card(t) for t in self.template]
        random.shuffle(self.library)
        self.revealed = None
        self.zones = {zone: [] for zone in ZONES}
        if self.commander:
            self.zones["command"].append(self._card(self.commander))
        self.life, self.mulligans, self.opening = START_LIFE, 0, True
        self.counts = {name: 0 for name in PLAYER_COUNTERS}
        self.mana = {color: 0 for color in MANA}
        self.turn, self.roll = 1, None
        self.log = []
        self._log("New game")
        self.draw(HAND_SIZE, save=False)
        if save:
            self._save()

    def clear(self) -> None:
        """Sweep the table: no deck, no cards."""
        self._empty()
        self._undo.clear()
        self._redo.clear()
        self._save()

    def keep(self) -> None:
        """Keep the opening hand: the game starts."""
        self.opening = False
        self._log("Kept the hand")
        self._save()

    # ---- the library
    @undoable
    def draw(self, count: int = 1, save: bool = True) -> int:
        drawn = 0
        self.revealed = None
        for _ in range(count):
            if not self.library:
                break
            self.zones["hand"].append(self.library.pop(0))
            drawn += 1
        if drawn:
            self._log(f"Drew {drawn} card{'s' if drawn != 1 else ''}")
        if save:
            self._save()
        return drawn

    @undoable
    def shuffle(self) -> None:
        random.shuffle(self.library)
        self.revealed = None
        self._log("Shuffled the library")
        self._save()

    @undoable
    def mulligan(self) -> None:
        """The hand goes back, the library is shuffled and seven new cards are drawn. Put as many cards on
        the bottom as you took mulligans (London mulligan) yourself."""
        self.library += self.zones["hand"]
        self.zones["hand"] = []
        random.shuffle(self.library)
        self.mulligans += 1
        self._log(f"Mulligan {self.mulligans}")
        self.draw(HAND_SIZE, save=False)
        self._save()

    @undoable
    def tutor(self, uid: str, to: str = "hand") -> None:
        """Take a chosen card out of the library into your hand (or another zone), then shuffle the library."""
        if not any(c["uid"] == uid for c in self.library):
            raise KeyError(uid)
        _, card = self._find(uid)
        self.library.remove(card)
        self.zones[to].append(card)
        random.shuffle(self.library)
        self.revealed = None
        self._log(f"Took {card['name']} from the library to {to}, shuffled")
        self._save()

    @undoable
    def top_to_bottom(self) -> None:
        if self.library:
            self.library.append(self.library.pop(0))
        self.revealed = None
        self._log("Top card to the bottom")
        self._save()

    @undoable
    def bottom_to_top(self) -> None:
        if self.library:
            self.library.insert(0, self.library.pop())
        self.revealed = None
        self._log("Bottom card to the top")
        self._save()

    @undoable
    def mill(self, count: int, to: str = "graveyard", bottom: bool = False) -> int:
        """The top (or bottom) cards of the library to a zone (the graveyard by default)."""
        moved = 0
        for _ in range(count):
            if not self.library:
                break
            card = self.library.pop(-1 if bottom else 0)
            self._reset(card)
            self.zones[to].append(card)
            moved += 1
        self.revealed = None
        self._log(f"Moved {moved} card{'s' if moved != 1 else ''} from the {'bottom' if bottom else 'top'} of the library to {to}")
        self._save()
        return moved

    @undoable
    def draw_random(self) -> None:
        """A random card of the library into your hand."""
        if self.library:
            self.zones["hand"].append(self.library.pop(random.randrange(len(self.library))))
        self.revealed = None
        self._log("Drew a random card")
        self._save()

    def reveal(self, show: bool) -> None:
        """Show the top card of the library to the others, or hide it again."""
        self.revealed = self._public_card(self.library[0]) if show and self.library else None
        self._log("Revealed the top card" if self.revealed else "Hid the revealed card")
        self._save()

    # ---- playing
    def _find(self, uid: str) -> tuple[list[dict], dict]:
        for zone in (*self.zones.values(), self.library):
            for card in zone:
                if card["uid"] == uid:
                    return zone, card
        raise KeyError(uid)

    def _place(self, card: dict, to: str, position: int | None = None) -> None:
        """Put a card in a zone or in the library. A token (a copy) does not go anywhere but the battlefield."""
        self._reset(card)
        if card.get("token") and to != "battlefield":
            return
        if to == "library-top":
            self.library.insert(0, card)
        elif to == "library-bottom":
            self.library.append(card)
        elif to == "library":
            self.library.insert(min((position or 1) - 1, len(self.library)), card)
        elif to == "library-shuffle":
            self.library.append(card)
            random.shuffle(self.library)
        else:
            self.zones[to].append(card)

    @undoable
    def move(self, uid: str, to: str, position: int | None = None) -> None:
        source, card = self._find(uid)
        source.remove(card)
        self.revealed = None
        self._place(card, to, position)
        self._log(f"Moved {card['name']} to {to}" if source is not self.library and not to.startswith("library")
                  else f"Moved a card to {to}")
        self._save()

    @undoable
    def move_all(self, zone: str, to: str) -> None:
        """Every card of a zone (or of the library) to another place."""
        source = self.library if zone == "library" else self.zones[zone]
        if (to in ZONES and self.zones[to] is source) or (zone == "library" and to.startswith("library")):
            return
        cards, source[:] = list(source), []
        for card in cards:
            self._place(card, to)
        self.revealed = None
        self._log(f"Moved all {len(cards)} cards of the {zone} to {to}")
        self._save()

    @undoable
    def discard_random(self) -> None:
        hand = self.zones["hand"]
        if hand:
            card = hand.pop(random.randrange(len(hand)))
            self._place(card, "graveyard")
            self._log(f"Discarded {card['name']} at random")
        self._save()

    @undoable
    def order_hand(self, by: str) -> None:
        rank = {"land": 0, "creature": 1, "other": 2}
        key = (lambda c: (rank.get(c["type"], 2), c["name"].lower())) if by == "type" else (lambda c: c["name"].lower())
        self.zones["hand"].sort(key=key)
        self._save()

    @undoable
    def untap_all(self) -> None:
        for card in self.zones["battlefield"]:
            card["tapped"] = False
        self._log("Untapped everything")
        self._save()

    @undoable
    def tap_all(self) -> None:
        for card in self.zones["battlefield"]:
            card["tapped"] = True
        self._log("Tapped everything")
        self._save()

    @undoable
    def proliferate(self) -> None:
        for card in self.zones["battlefield"]:
            for kind, n in card["counters"].items():
                card["counters"][kind] = min(n + 1, MAX_COUNTERS)
        self._log("Proliferated")
        self._save()

    @undoable
    def next_turn(self) -> None:
        """Untap everything, draw a card and count the turn; the mana pool and the storm count start again."""
        for card in self.zones["battlefield"]:
            card["tapped"] = False
        self.turn += 1
        self.counts["storm"] = 0
        self.mana = {color: 0 for color in MANA}
        self._log(f"Turn {self.turn}")
        self.draw(1, save=False)
        self._save()

    @undoable
    def patch(self, uid: str, patch: CardPatch) -> None:
        zone, card = self._find(uid)
        if zone is not self.zones["battlefield"]:
            raise KeyError(uid)  # only cards on the battlefield tap or get counters
        if patch.tapped is not None:
            card["tapped"] = patch.tapped
        if patch.clear_counters:
            card["counters"] = {}
        if patch.counter:
            kind = patch.counter.type.strip()
            n = max(0, min(card["counters"].get(kind, 0) + patch.counter.delta, MAX_COUNTERS))
            if n:
                card["counters"][kind] = n
            else:
                card["counters"].pop(kind, None)
        for field in ("facedown", "phased", "rot"):
            if getattr(patch, field) is not None:
                card[field] = getattr(patch, field)
        if patch.power_delta:
            card["dp"] = max(-MAX_COUNTERS, min(card.get("dp", 0) + patch.power_delta, MAX_COUNTERS))
        if patch.toughness_delta:
            card["dt"] = max(-MAX_COUNTERS, min(card.get("dt", 0) + patch.toughness_delta, MAX_COUNTERS))
        self._save()

    @undoable
    def copy_card(self, uid: str, count: int = 1) -> None:
        """Token copies of a card on the battlefield; a copy ceases to exist when it leaves."""
        zone, card = self._find(uid)
        if zone is not self.zones["battlefield"]:
            raise KeyError(uid)
        for _ in range(count):
            clone = {**self._card({"name": card["name"], "image_url": card["image_url"]}), "type": card["type"], "token": True}
            self.zones["battlefield"].append(clone)
        self._log(f"Made {count} token cop{'ies' if count != 1 else 'y'} of {card['name']}")
        self._save()

    @undoable
    def delete_card(self, uid: str) -> None:
        """Remove a card from the game altogether."""
        zone, card = self._find(uid)
        zone.remove(card)
        self.revealed = None
        self._log(f"Removed {card['name']} from the game")
        self._save()

    @undoable
    def set_life(self, patch: LifePatch) -> None:
        if patch.life is not None:
            self.life = patch.life
        if patch.life_delta:
            self.life += patch.life_delta
        for name in PLAYER_COUNTERS:
            delta = getattr(patch, f"{name}_delta")
            if delta:
                self.counts[name] = max(0, self.counts[name] + delta)
        self._save()

    def set_mana(self, patch: ManaPatch) -> None:
        if patch.clear or patch.color == "all":
            self.mana = {color: 0 for color in MANA}
        elif patch.delta:
            self.mana[patch.color] = max(0, min(self.mana[patch.color] + patch.delta, MAX_COUNTERS))
        self._save()

    def roll_die(self, kind: str) -> dict:
        result = random.randint(1, DICE[kind])
        self.roll = {"kind": kind, "result": ("heads" if result == 1 else "tails") if kind == "coin" else result}
        self._log(f"Rolled {kind}: {self.roll['result']}")
        self._save()
        return self.roll

    def undo(self) -> bool:
        if not self._undo:
            return False
        self._redo.append(copy.deepcopy(self._dump()))
        self._apply(self._undo.pop())
        self._log("Undo")
        self._save()
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        self._undo.append(copy.deepcopy(self._dump()))
        self._apply(self._redo.pop())
        self._log("Redo")
        self._save()
        return True

    # ---- what is shown to whom
    @staticmethod
    def _public_card(card: dict, hide: bool = False) -> dict:
        """A card as shown to someone. A face-down card on the battlefield gives away nothing to the others."""
        out = {k: card[k] for k in ("uid", "name", "image_url", "type", "tapped")}
        out["counters"] = [{"t": kind, "n": n} for kind, n in (card.get("counters") or {}).items()]
        out.update({"facedown": bool(card.get("facedown")), "phased": bool(card.get("phased")), "rot": bool(card.get("rot")),
                    "dp": card.get("dp", 0), "dt": card.get("dt", 0), "token": bool(card.get("token"))})
        if hide and out["facedown"]:
            out.update({"name": "Face-down card", "image_url": "", "type": "creature"})
        return out

    def public(self) -> dict:
        """What the overlay shows: no hand and no library order, only how many cards they hold."""
        return {
            "loaded": self.loaded, "deck": self.deck_name, "rev": self.rev, "life": self.life, "turn": self.turn,
            **{name: n for name, n in self.counts.items()},
            "hand": len(self.zones["hand"]), "library": len(self.library), "revealed": self.revealed, "roll": self.roll,
            "battlefield": [self._public_card(c, hide=True) for c in self.zones["battlefield"]],
            "graveyard": [self._public_card(c) for c in self.zones["graveyard"]],
            "exile": [self._public_card(c) for c in self.zones["exile"]],
            "command": [self._public_card(c) for c in self.zones["command"]],
        }

    def private(self, peek: int = 0, library: bool = False, bottom: int = 0) -> dict:
        """What only you see, on the playtest page: your hand and, if you look, parts of your library."""
        peek, bottom = max(0, peek), min(max(0, bottom), len(self.library))
        return {
            "rev": self.rev, "mulligans": self.mulligans, "opening": self.opening, "mana": self.mana,
            "log": self.log[-100:],
            "can_undo": bool(self._undo), "can_redo": bool(self._redo),
            # what the face-down cards on the battlefield really are: only you may know
            "hidden": {c["uid"]: {"name": c["name"], "image_url": c["image_url"]}
                       for c in self.zones["battlefield"] if c.get("facedown")},
            "hand": [self._public_card(c) for c in self.zones["hand"]],
            "top": [self._public_card(c) for c in self.library[:peek]],
            "bottom": [self._public_card(c) for c in self.library[len(self.library) - bottom:]],
            # the whole library by name, to pick a card from (the order of the library is not given away)
            "library": sorted((self._public_card(c) for c in self.library), key=lambda c: c["name"].lower()) if library else [],
        }
