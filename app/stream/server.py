"""MTG Token Overlay: an OBS overlay showing MTG tokens, part of the MTG Collection Manager.

The app mounts it at /stream: /stream/control is the control panel, /stream/overlay the Browser
source for OBS. Its state is kept in its own JSON files (STREAM_STATE, default stream-state.json).
"""

from __future__ import annotations

import asyncio
import functools
import hashlib
import json
import os
import re
import secrets
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, RedirectResponse, Response
from pydantic import BaseModel, Field, field_validator

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
DEFAULT_STATE_PATH = Path("stream-state.json")


def code_fingerprint() -> str:
    """Short hash of this file, to tell whether the running server is outdated."""
    try:
        return hashlib.sha1(Path(__file__).read_bytes()).hexdigest()[:10]
    except OSError:
        return "unknown"

ALLOWED_IMAGE_PREFIX = "https://cards.scryfall.io/"
MAX_TOKENS = 30
MAX_COUNT = 999
MAX_COUNTERS = 99
SPLIT_OFFSET = 0.03  # fraction of the overlay width
MAX_KEYWORDS = 12
MAX_VALUE = 24
UNDO_LIMIT = 50
MAX_PRESETS = 40
# Loose counters you can put in the overlay for cards on the table: the two
# P/T counters plus keyword counters (Ikoria-style).
PT_COUNTERS = ("+1/+1", "-1/-1")
KEYWORD_COUNTERS = (
    "Flying", "First strike", "Double strike", "Deathtouch", "Defender", "Haste",
    "Hexproof", "Indestructible", "Lifelink", "Menace", "Reach", "Shroud",
    "Trample", "Vigilance", "Ward",
)
# Mana in your pool: white, blue, black, red, green, colorless.
MANA_COUNTERS = ("W", "U", "B", "R", "G", "C")
COUNTER_TYPES = PT_COUNTERS + KEYWORD_COUNTERS + MANA_COUNTERS
# The turn tracker: the phases and steps of a turn, in order.
PHASES = ("untap", "upkeep", "draw", "main1", "combat", "main2", "end")
MAX_TURN = 999
# Tallies shown on stream: how often you cast your commander (the tax is 2 per cast) and the
# spells cast this turn (storm, back to 0 every new turn).
MAX_CASTS = 99
MAX_STORM = 999
TALLY_LIMITS = {"casts": MAX_CASTS, "storm": MAX_STORM}
SPOTLIGHT_SECONDS = (2, 30)


# Stream Deck: every action at a plain address (/stream/sd/<action>), so the Website action that
# comes with the Stream Deck software can press it ("GET request in background"), no plugin needed.
# In this order they fill a Stream Deck XL (8 × 4); the last four keys are your first four presets.
STREAMDECK_ACTIONS = (
    ("next-phase", "Next phase"), ("prev-phase", "Previous phase"), ("untap", "Untap all"),
    ("attack-all", "Attack with all"), ("undo", "Undo"), ("intro", "Deck intro"),
    ("spotlight-hide", "Hide spotlight"), ("new-game", "New game"),
    ("mana-W", "White mana"), ("mana-U", "Blue mana"), ("mana-B", "Black mana"), ("mana-R", "Red mana"),
    ("mana-G", "Green mana"), ("mana-C", "Colorless mana"), ("mana-empty", "Empty mana pool"), ("d20", "Roll a d20"),
    ("commander", "Cast commander"), ("commander-back", "Commander cast back"), ("storm", "Spell for storm"),
    ("storm-back", "Storm one back"), ("plus-counter", "+1/+1 counter"), ("minus-counter", "−1/−1 counter"),
    ("coin", "Flip a coin"), ("d6", "Roll a d6"),
    ("tracker", "Show or hide the turn tracker"), ("win", "I won"), ("loss", "I lost"),
    ("clear", "Clear the board"),
)
# Still pressable, though not on the ready-made layout (keys set up earlier keep working).
STREAMDECK_EXTRA = ("tracker-on", "tracker-off", "2d6", "draw")
STREAMDECK_PRESET_KEYS = 4
MANA_SYMBOLS = ("W", "U", "B", "R", "G", "C")


def default_turn() -> dict:
    return {"on": False, "number": 1, "phase": "untap", "casts": 0, "storm": 0}


def clean_turn(raw: Any) -> dict:
    turn = default_turn()
    if isinstance(raw, dict):
        if isinstance(raw.get("on"), bool):
            turn["on"] = raw["on"]
        if isinstance(raw.get("number"), int) and 1 <= raw["number"] <= MAX_TURN:
            turn["number"] = raw["number"]
        if raw.get("phase") in PHASES:
            turn["phase"] = raw["phase"]
        for name, limit in TALLY_LIMITS.items():
            if isinstance(raw.get(name), int) and 0 <= raw[name] <= limit:
                turn[name] = raw[name]
    return turn
MANUAL_PT = {"ge": -99, "le": 999}


# ---------------------------------------------------------------- models


class Position(BaseModel):
    """Top-left corner of a stack as a fraction of the overlay (0..1), so it
    works at any canvas resolution."""

    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)


class Token(BaseModel):
    """One stack of identical tokens. Split stacks each get their own id.

    kind "counter" is a loose counter (e.g. "+1/+1") without a card image; its
    name is the counter type and image_url is empty."""

    id: str
    kind: Literal["card", "counter"] = "card"
    name: str
    image_url: str
    count: int
    tapped: bool = False
    # Net number of counters: positive = +1/+1, negative = -1/-1. In MTG
    # +1/+1 and -1/-1 counters cancel each other out, so one number is enough.
    counters: int = 0
    power: str | None = None
    toughness: str | None = None
    # Keyword abilities as printed (e.g. ["Flying", "Vigilance"]).
    keywords: list[str] = []
    # Manual power/toughness for tokens printed as */* (None = not set).
    set_power: int | None = None
    set_toughness: int | None = None
    # Extra value for a loose counter, e.g. the cost of Ward ("2", "Pay 3 life").
    value: str | None = None
    # None = in the default row; otherwise dragged to this spot.
    pos: Position | None = None


class TokenIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    image_url: str = Field(max_length=500)
    power: str | None = Field(default=None, max_length=5)
    toughness: str | None = Field(default=None, max_length=5)
    keywords: list[str] = Field(default_factory=list, max_length=MAX_KEYWORDS)

    @field_validator("keywords")
    @classmethod
    def clean_keywords(cls, v: list[str]) -> list[str]:
        out = []
        for k in v:
            k = k.strip()[:40]
            if k and k not in out:
                out.append(k)
        return out

    @field_validator("name")
    @classmethod
    def strip_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name must not be empty")
        return v

    @field_validator("image_url")
    @classmethod
    def check_image_url(cls, v: str) -> str:
        if not v.startswith(ALLOWED_IMAGE_PREFIX):
            raise ValueError(f"image must start with {ALLOWED_IMAGE_PREFIX}")
        return v


class TokenPatch(BaseModel):
    """All fields are optional; only what you send is changed."""

    delta: int | None = Field(default=None, ge=-MAX_COUNT, le=MAX_COUNT)
    # Set the number directly (typed in the control panel); 0 removes the stack.
    count: int | None = Field(default=None, ge=0, le=MAX_COUNT)
    tapped: bool | None = None
    counter_delta: int | None = Field(default=None, ge=-MAX_COUNTERS, le=MAX_COUNTERS)
    # Send {"pos": {"x": .., "y": ..}} to place a stack, {"pos": null} to put
    # it back in the row. Leaving the field out keeps the position.
    pos: Position | None = None
    # Manual P/T for */* tokens; send null to clear.
    set_power: int | None = Field(default=None, **MANUAL_PT)
    set_toughness: int | None = Field(default=None, **MANUAL_PT)
    # Value of a loose counter (Ward cost); send null or "" to clear.
    value: str | None = Field(default=None, max_length=MAX_VALUE)

    @field_validator("value")
    @classmethod
    def clean_value(cls, v: str | None) -> str | None:
        return clean_counter_value(v)


def clean_counter_value(v: str | None) -> str | None:
    v = (v or "").strip()
    return v or None


class CounterIn(BaseModel):
    type: str
    value: str | None = Field(default=None, max_length=MAX_VALUE)

    @field_validator("value")
    @classmethod
    def clean_value(cls, v: str | None) -> str | None:
        return clean_counter_value(v)

    @field_validator("type")
    @classmethod
    def known_type(cls, v: str) -> str:
        if v not in COUNTER_TYPES:
            raise ValueError(f"type must be one of: {', '.join(COUNTER_TYPES)}")
        return v


# Tokens with an ability you can "Use" from the control panel. tap / sac say
# whether the cost includes {T} and sacrificing it; text is shown on stream.
ABILITIES = {
    "treasure":   {"cost": "{T}, Sacrifice",                "effect": "Add one mana of any color",
                   "tap": True,  "sac": True,  "text": "+1 mana", "mana": "any"},
    "gold":       {"cost": "Sacrifice",                     "effect": "Add one mana of any color",
                   "tap": False, "sac": True,  "text": "+1 mana", "mana": "any"},
    "food":       {"cost": "{2}, {T}, Sacrifice",           "effect": "You gain 3 life",
                   "tap": True,  "sac": True,  "text": "+3 life"},
    "clue":       {"cost": "{2}, Sacrifice",                "effect": "Draw a card",
                   "tap": False, "sac": True,  "text": "Draw a card"},
    "blood":      {"cost": "{1}, {T}, Discard a card, Sacrifice", "effect": "Draw a card",
                   "tap": True,  "sac": True,  "text": "Rummage"},
    "map":        {"cost": "{1}, {T}, Sacrifice",           "effect": "Target creature you control explores",
                   "tap": True,  "sac": True,  "text": "Explore"},
    "powerstone": {"cost": "{T}",                           "effect": "Add {C} (not for nonartifact spells)",
                   "tap": True,  "sac": False, "text": "+1 colorless", "mana": "C"},
}


# Dice and coin rolls shown on stream: kind -> number of sides.
ROLL_SIDES = {"coin": 2, "d4": 4, "d6": 6, "d8": 8, "d10": 10, "d12": 12, "d20": 20}


class RollIn(BaseModel):
    kind: Literal["coin", "d4", "d6", "d8", "d10", "d12", "d20"]
    count: int = Field(default=1, ge=1, le=6)


class SplitIn(BaseModel):
    count: int = Field(default=1, ge=1, le=MAX_COUNT)


class TurnPatch(BaseModel):
    """Turn tracker: show/hide it, jump to a phase, set the turn number."""
    on: bool | None = None
    phase: Literal["untap", "upkeep", "draw", "main1", "combat", "main2", "end"] | None = None
    number: int | None = Field(default=None, ge=1, le=MAX_TURN)
    casts: int | None = Field(default=None, ge=0, le=MAX_CASTS)
    storm: int | None = Field(default=None, ge=0, le=MAX_STORM)


class ResultIn(BaseModel):
    """How the game ended: shown big on stream and, for a deck from the collection, put in the game log."""
    result: Literal["win", "loss", "draw"]
    log: bool = True


class TallyIn(BaseModel):
    delta: int = Field(default=1, ge=-MAX_STORM, le=MAX_STORM)


class SpotlightIn(BaseModel):
    """One card shown big on stream for a few seconds."""
    name: str = Field(min_length=1, max_length=150)
    image_url: str
    seconds: int = Field(default=7, ge=SPOTLIGHT_SECONDS[0], le=SPOTLIGHT_SECONDS[1])

    @field_validator("image_url")
    @classmethod
    def check_image_url(cls, v: str) -> str:
        if not v.startswith(ALLOWED_IMAGE_PREFIX):
            raise ValueError("only Scryfall images are allowed")
        return v


class IntroIn(BaseModel):
    """The deck intro: a bar with the commander, the deck name, its bracket and record."""
    name: str = Field(min_length=1, max_length=120)
    commander: str | None = Field(default=None, max_length=200)
    image_url: str | None = None
    bracket: str | None = Field(default=None, max_length=60)
    record: str | None = Field(default=None, max_length=60)

    @field_validator("image_url")
    @classmethod
    def check_image_url(cls, v: str | None) -> str | None:
        return v if v and v.startswith(ALLOWED_IMAGE_PREFIX) else None


class UseIn(BaseModel):
    # For "any color" mana (Treasure, Gold): which colour goes into the pool.
    color: Literal["W", "U", "B", "R", "G"] | None = None


class PresetIn(BaseModel):
    name: str = Field(min_length=1, max_length=40)

    @field_validator("name")
    @classmethod
    def strip_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name must not be empty")
        return v


def is_creature(t: Token) -> bool:
    return t.kind == "card" and t.power is not None


def has_vigilance(t: Token) -> bool:
    return any(k.lower().startswith("vigilance") for k in t.keywords)


def mutating(method):
    """Remember the board (and the turn) before a change so it can be undone.
    If the change fails, everything is put back as it was."""
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        before = [t.model_copy(deep=True) for t in self.tokens]
        turn_before = dict(self.turn)
        try:
            result = method(self, *args, **kwargs)
        except BaseException:
            self.tokens = before
            self.turn = turn_before
            raise
        # Moving things around isn't an undo step (dragging sends many of
        # those), so only remember changes to anything but positions.
        if self.turn != turn_before or [t.model_copy(update={"pos": None}) for t in before] != \
                [t.model_copy(update={"pos": None}) for t in self.tokens]:
            self.history.append((before, turn_before))
            del self.history[:-UNDO_LIMIT]
        return result
    return wrapper


def same_kind(a: Token, b: Token) -> bool:
    """Two stacks can be merged if they look exactly the same on the table."""
    return (
        a.kind == b.kind
        and a.name == b.name
        and a.image_url == b.image_url
        and a.tapped == b.tapped
        and a.counters == b.counters
        and a.set_power == b.set_power
        and a.set_toughness == b.set_toughness
        and a.value == b.value
    )


# ---------------------------------------------------------------- storage


class TokenStore:
    """List of token stacks, written to disk atomically after every change."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.turn: dict = default_turn()
        self.tokens: list[Token] = self._load()
        self.history: list[tuple[list[Token], dict]] = []   # earlier boards, for undo

    def _load(self) -> list[Token]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            tokens = [Token(**t) for t in raw.get("tokens", [])][:MAX_TOKENS]
        except (OSError, ValueError, TypeError, AttributeError):
            # Missing or broken file: just start empty.
            return []
        self.turn = clean_turn(raw.get("turn"))
        return tokens

    def _save(self) -> None:
        data = json.dumps(self.snapshot(), ensure_ascii=False, indent=2)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".state-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(data)
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def snapshot(self) -> dict:
        return {"tokens": [t.model_dump() for t in self.tokens], "turn": dict(self.turn)}

    def _find(self, token_id: str) -> Token:
        for t in self.tokens:
            if t.id == token_id:
                return t
        raise KeyError(token_id)

    @mutating
    def add(self, item: TokenIn) -> Token:
        # A new token enters untapped and without counters, so only add it to
        # a stack in that state.
        for t in self.tokens:
            if t.kind == "card" and t.image_url == item.image_url and not t.tapped and t.counters == 0:
                t.count = min(t.count + 1, MAX_COUNT)
                self._save()
                return t
        return self._append(Token(id=uuid.uuid4().hex[:12], count=1, **item.model_dump()))

    @mutating
    def add_counter(self, counter_type: str, value: str | None = None) -> Token:
        """Add one loose counter to the first pile with the same type and value."""
        return self._put_counter(counter_type, value)

    def _put_counter(self, counter_type: str, value: str | None = None) -> Token:
        for t in self.tokens:
            if t.kind == "counter" and t.name == counter_type and t.value == value:
                t.count = min(t.count + 1, MAX_COUNT)
                self._save()
                return t
        return self._append(Token(id=uuid.uuid4().hex[:12], kind="counter", name=counter_type,
                                  image_url="", count=1, value=value))

    def _append(self, token: Token) -> Token:
        if len(self.tokens) >= MAX_TOKENS:
            raise OverflowError
        self.tokens.append(token)
        self._save()
        return token

    @mutating
    def update(self, token_id: str, patch: TokenPatch) -> Token | None:
        token = self._find(token_id)
        if patch.tapped is not None:
            token.tapped = patch.tapped
        if patch.counter_delta:
            token.counters = max(-MAX_COUNTERS, min(token.counters + patch.counter_delta, MAX_COUNTERS))
        if "pos" in patch.model_fields_set:
            token.pos = patch.pos
        if "set_power" in patch.model_fields_set:
            token.set_power = patch.set_power
        if "set_toughness" in patch.model_fields_set:
            token.set_toughness = patch.set_toughness
        if "value" in patch.model_fields_set:
            token.value = patch.value
        if patch.count is not None:
            token.count = patch.count
        if patch.delta:
            token.count = min(token.count + patch.delta, MAX_COUNT)
        if patch.count is not None or patch.delta:
            if token.count <= 0:
                self.tokens.remove(token)
                token = None
        self._save()
        return token

    @mutating
    def use(self, token_id: str, color: str | None = None) -> tuple[Token, dict]:
        """Activate the ability of one token from a stack (see ABILITIES). Mana
        abilities also put the mana in the pool (Treasure/Gold: `color`)."""
        token = self._find(token_id)
        ability = ABILITIES.get(token.name.lower()) if token.kind == "card" else None
        if not ability:
            raise TypeError
        if ability["tap"] and token.tapped:
            raise PermissionError       # a tapped token can't pay {T}
        if ability["sac"]:
            token.count -= 1
            if token.count <= 0:
                self.tokens.remove(token)
        elif token.count == 1:
            token.tapped = True
        else:
            # Tap just one of the stack: it moves to its own (tapped) stack.
            if len(self.tokens) >= MAX_TOKENS:
                raise OverflowError
            token.count -= 1
            tapped = token.model_copy(update={"id": uuid.uuid4().hex[:12], "count": 1, "tapped": True})
            if token.pos:
                tapped.pos = Position(x=min(token.pos.x + SPLIT_OFFSET, 1), y=token.pos.y)
            self.tokens.insert(self.tokens.index(token) + 1, tapped)
        mana = color if ability.get("mana") == "any" else ability.get("mana")
        if mana:
            self._put_counter(mana)
        self._save()
        return token, ability

    @mutating
    def sacrifice(self, token_id: str, count: int) -> tuple[Token, int]:
        """Take up to `count` tokens off a card stack; returns (stack, how many)."""
        token = self._find(token_id)
        if token.kind != "card":
            raise TypeError
        n = min(count, token.count)
        token.count -= n
        if token.count <= 0:
            self.tokens.remove(token)
        self._save()
        return token, n

    @mutating
    def split(self, token_id: str, count: int) -> Token:
        """Move `count` tokens off a stack into a new stack right after it."""
        token = self._find(token_id)
        if count >= token.count:
            raise ValueError
        if len(self.tokens) >= MAX_TOKENS:
            raise OverflowError
        new = token.model_copy(update={"id": uuid.uuid4().hex[:12], "count": count})
        if token.pos:
            # Fan the new stack out a little so it doesn't hide the original.
            new.pos = Position(x=min(token.pos.x + SPLIT_OFFSET, 1), y=token.pos.y)
        token.count -= count
        self.tokens.insert(self.tokens.index(token) + 1, new)
        self._save()
        return new

    @mutating
    def merge(self, token_id: str) -> Token:
        """Merge every stack that looks the same into this one."""
        token = self._find(token_id)
        others = [t for t in self.tokens if t is not token and same_kind(t, token)]
        for t in others:
            token.count = min(token.count + t.count, MAX_COUNT)
            self.tokens.remove(t)
        self._save()
        return token

    @mutating
    def attack(self, token_id: str) -> Token:
        """A creature stack attacks: it taps (unless it has vigilance)."""
        token = self._find(token_id)
        if not is_creature(token):
            raise TypeError
        if token.tapped:
            raise PermissionError
        if not has_vigilance(token):
            token.tapped = True
        self._save()
        return token

    @mutating
    def attack_all(self) -> list[str]:
        """Every untapped creature stack attacks; returns their ids."""
        attackers = [t for t in self.tokens if is_creature(t) and not t.tapped]
        for t in attackers:
            if not has_vigilance(t):
                t.tapped = True
        self._save()
        return [t.id for t in attackers]

    @mutating
    def copy(self, token_id: str, count: int) -> Token:
        """Create `count` copies of a token. Copies enter untapped and without
        counters, so they join a stack in that state (or start a new one)."""
        token = self._find(token_id)
        if token.kind != "card":
            raise TypeError
        for t in self.tokens:
            if t.kind == "card" and t.image_url == token.image_url and not t.tapped and t.counters == 0:
                t.count = min(t.count + count, MAX_COUNT)
                self._save()
                return t
        if len(self.tokens) >= MAX_TOKENS:
            raise OverflowError
        new = token.model_copy(update={"id": uuid.uuid4().hex[:12], "count": min(count, MAX_COUNT),
                                       "tapped": False, "counters": 0})
        if token.pos:
            new.pos = Position(x=min(token.pos.x + SPLIT_OFFSET, 1), y=token.pos.y)
        self.tokens.insert(self.tokens.index(token) + 1, new)
        self._save()
        return new

    @mutating
    def replace_all(self, tokens: list[Token]) -> None:
        """Put a whole new board in place (loading a preset)."""
        self.tokens = tokens[:MAX_TOKENS]
        self._save()

    def undo(self) -> None:
        """Go back to the board before the last change. Positions stay where
        they are now (moving isn't an undo step)."""
        if not self.history:
            raise LookupError
        board, turn = self.history.pop()
        now = {t.id: t.pos for t in self.tokens}
        for t in board:
            if t.id in now:
                t.pos = now[t.id]
        self.tokens = board
        self.turn = turn
        self._save()

    # ---- turn tracker

    @mutating
    def next_phase(self) -> dict:
        """Go to the next phase. Mana empties between steps; after the end
        step a new turn starts and everything untaps."""
        mana = [t for t in self.tokens if t.kind == "counter" and t.name in MANA_COUNTERS]
        for t in mana:
            self.tokens.remove(t)
        i = PHASES.index(self.turn["phase"])
        new_turn = i == len(PHASES) - 1
        untapped = 0
        if new_turn:
            self.turn["number"] = min(self.turn["number"] + 1, MAX_TURN)
            self.turn["phase"] = PHASES[0]
            self.turn["storm"] = 0  # storm counts the spells of this turn only
            untapped = sum(1 for t in self.tokens if t.tapped)
            for t in self.tokens:
                t.tapped = False
        else:
            self.turn["phase"] = PHASES[i + 1]
        self.turn["on"] = True
        self._save()
        return {"new_turn": new_turn, "untapped": untapped, "emptied": bool(mana)}

    @mutating
    def prev_phase(self) -> None:
        """One phase back (no automatic effects); before untap is the previous turn's end."""
        i = PHASES.index(self.turn["phase"])
        if i == 0:
            if self.turn["number"] > 1:
                self.turn["number"] -= 1
                self.turn["phase"] = PHASES[-1]
        else:
            self.turn["phase"] = PHASES[i - 1]
        self._save()

    @mutating
    def set_turn(self, patch: "TurnPatch") -> None:
        if patch.on is not None:
            self.turn["on"] = patch.on
        if patch.number is not None:
            self.turn["number"] = patch.number
        if patch.phase is not None:
            self.turn["phase"] = patch.phase
        if patch.casts is not None:
            self.turn["casts"] = patch.casts
        if patch.storm is not None:
            self.turn["storm"] = patch.storm
        self._save()

    @mutating
    def tally(self, name: str, delta: int) -> int:
        """One more (or less) commander cast or spell for storm; never below 0."""
        self.turn[name] = max(0, min(self.turn[name] + delta, TALLY_LIMITS[name]))
        self._save()
        return self.turn[name]

    def reset_positions(self) -> None:
        for t in self.tokens:
            t.pos = None
        self._save()

    @mutating
    def empty_mana_pool(self) -> int:
        """Remove all mana from the pool (like at the end of a step)."""
        mana = [t for t in self.tokens if t.kind == "counter" and t.name in MANA_COUNTERS]
        for t in mana:
            self.tokens.remove(t)
        self._save()
        return len(mana)

    @mutating
    def untap_all(self) -> None:
        for t in self.tokens:
            t.tapped = False
        self._save()

    @mutating
    def remove(self, token_id: str) -> None:
        self.tokens.remove(self._find(token_id))
        self._save()

    @mutating
    def clear(self) -> None:
        self.tokens.clear()
        self._save()


# ---------------------------------------------------------------- deck import
#
# Paste an Archidekt or Moxfield link: the server reads the deck list, asks
# Scryfall which tokens those cards make (their "all_parts") and keeps that
# list, so the control panel can show them as one-click buttons. Done on the
# server because those sites don't allow it from the browser.

DECK_SOURCES = {
    "archidekt": re.compile(r"^https?://(?:www\.)?archidekt\.com/(?:api/)?decks/(\d+)", re.I),
    "moxfield": re.compile(r"^https?://(?:www\.)?moxfield\.com/decks/([A-Za-z0-9_-]+)", re.I),
}
USER_AGENT = "MTGTokenOverlay/1.0 (local OBS overlay)"
SCRYFALL_BATCH = 75          # Scryfall's /cards/collection takes at most 75 at once


class DeckError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def http_json(url: str, body: Any = None) -> Any:
    """GET, or POST a JSON body, and return the parsed JSON."""
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403, 404):
            raise DeckError(404, "deck not found (is it public?)")
        raise DeckError(502, f"the site answered with an error ({e.code}), try again later")
    except (urllib.error.URLError, TimeoutError, ValueError):
        raise DeckError(502, "couldn't reach the site, check your internet connection")


def parse_deck_url(url: str) -> tuple[str, str]:
    for source, pattern in DECK_SOURCES.items():
        m = pattern.match(url.strip())
        if m:
            return source, m.group(1)
    raise DeckError(400, "paste a link to an Archidekt or Moxfield deck")


def deck_cards(source: str, deck_id: str) -> tuple[str, list[str]]:
    """The deck's name and the Scryfall ids of the cards that are played
    (main deck, commanders, companions; not side- or maybeboard)."""
    ids: list[str] = []
    if source == "archidekt":
        data = http_json(f"https://archidekt.com/api/decks/{deck_id}/")
        included = {c.get("name") for c in data.get("categories", []) if c.get("includedInDeck", True)}
        for entry in data.get("cards", []):
            cats = entry.get("categories") or []
            if cats and not any(c in included for c in cats):
                continue
            uid = (entry.get("card") or {}).get("uid")
            if uid:
                ids.append(uid)
    else:
        data = http_json(f"https://api2.moxfield.com/v3/decks/all/{deck_id}")
        boards = data.get("boards") or {}
        for board in ("commanders", "companions", "mainboard"):
            for entry in ((boards.get(board) or {}).get("cards") or {}).values():
                sid = (entry.get("card") or {}).get("scryfall_id")
                if sid:
                    ids.append(sid)
    return str(data.get("name") or "Deck"), list(dict.fromkeys(ids))


def scryfall_cards(ids: list[str]) -> list[dict]:
    cards: list[dict] = []
    for i in range(0, len(ids), SCRYFALL_BATCH):
        if i:
            time.sleep(0.1)             # Scryfall asks for ~10 requests per second at most
        batch = [{"id": x} for x in ids[i:i + SCRYFALL_BATCH]]
        cards += http_json("https://api.scryfall.com/cards/collection", {"identifiers": batch}).get("data", [])
    return cards


def ward_cost(text: str) -> str:
    m = re.search(r"\bWard(?: ((?:\{[^}\n]+\})+)|—([^\n.(\",]+))", text or "")
    if not m:
        return ""
    return m.group(1) or f"— {m.group(2).strip()}"


def token_info(card: dict) -> dict | None:
    """What the control panel needs to put this token on the board."""
    faces = card.get("card_faces") or []
    face = card if card.get("image_uris") else (faces[0] if faces and faces[0].get("image_uris") else None)
    if not face:
        return None
    image = face["image_uris"].get("normal", "")
    if not image.startswith(ALLOWED_IMAGE_PREFIX):
        return None
    src = face if face.get("power") is not None else card
    text = face.get("oracle_text") or card.get("oracle_text") or ""
    keywords = [k for k in card.get("keywords", []) if not faces or k.lower() in text.lower()]
    keywords = [f"Ward {ward_cost(text)}" if k == "Ward" and ward_cost(text) else k for k in keywords]
    return {
        "name": face.get("name") or card.get("name"),
        "image_url": image,
        "power": src.get("power"),
        "toughness": src.get("toughness"),
        "keywords": keywords[:MAX_KEYWORDS],
        "type_line": face.get("type_line") or card.get("type_line") or "",
        "text": text,
    }


def find_deck_tokens(url: str) -> dict:
    source, deck_id = parse_deck_url(url)
    name, ids = deck_cards(source, deck_id)
    if not ids:
        raise DeckError(404, "no cards found in that deck")
    makers: dict[str, set[str]] = {}
    for card in scryfall_cards(ids):
        for part in card.get("all_parts") or []:
            if part.get("component") == "token" and part.get("id") and part["id"] != card.get("id"):
                makers.setdefault(part["id"], set()).add(card.get("name", ""))
    # One entry per distinct token (same name and rules), with every card that makes it.
    merged: dict[tuple, dict] = {}
    for card in scryfall_cards(list(makers)):
        info = token_info(card)
        if not info:
            continue
        key = (info["name"], info["power"], info["toughness"], info["text"])
        entry = merged.setdefault(key, {**info, "made_by": set()})
        entry["made_by"] |= makers.get(card["id"], set())
    tokens = sorted(merged.values(), key=lambda t: (t["name"].lower(), t["text"]))
    for t in tokens:
        t["made_by"] = sorted(t["made_by"])
    return {"name": name, "url": url.strip(), "source": source, "cards": len(ids), "tokens": tokens}


class DeckStore:
    """The last imported deck and its tokens, kept in its own file."""

    def __init__(self, path: Path):
        self.path = Path(path)
        try:
            self.deck: dict | None = json.loads(self.path.read_text(encoding="utf-8")) or None
        except (OSError, ValueError):
            self.deck = None

    def save(self, deck: dict | None) -> None:
        self.deck = deck
        if deck is None:
            self.path.unlink(missing_ok=True)
            return
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".deck-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(json.dumps(deck, ensure_ascii=False, indent=2))
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise


class DeckIn(BaseModel):
    url: str = Field(min_length=1, max_length=300)


class PresetStore:
    """Saved boards per deck ("Krenko": Goblins and Treasures), in their own file."""

    def __init__(self, path: Path):
        self.path = Path(path)
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self.presets: dict[str, list[dict]] = {
                str(k): list(v) for k, v in raw.get("presets", {}).items()}
        except (OSError, ValueError, TypeError, AttributeError):
            self.presets = {}

    def _save(self) -> None:
        data = json.dumps({"presets": self.presets}, ensure_ascii=False, indent=2)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".presets-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(data)
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def summary(self) -> list[dict]:
        return [{"name": name, "stacks": len(items), "tokens": sum(i.get("count", 1) for i in items)}
                for name, items in sorted(self.presets.items(), key=lambda kv: kv[0].lower())]

    def save(self, name: str, tokens: list[Token]) -> None:
        if name not in self.presets and len(self.presets) >= MAX_PRESETS:
            raise OverflowError
        # Stored fresh: untapped, no ids (they get new ones when loaded).
        self.presets[name] = [t.model_dump(exclude={"id"}) | {"tapped": False} for t in tokens]
        self._save()

    def board(self, name: str) -> list[Token]:
        items = self.presets[name]
        out = []
        for item in items:
            try:
                out.append(Token(id=uuid.uuid4().hex[:12], **item))
            except (TypeError, ValueError):
                continue                # skip anything broken in the file
        return out

    def delete(self, name: str) -> None:
        del self.presets[name]
        self._save()


# ---------------------------------------------------------------- websockets


class Hub:
    """Tracks open websockets and sends every new state to all of them."""

    def __init__(self):
        self.clients: set[WebSocket] = set()
        # New on every start. Pages that see a different id reload themselves,
        # so OBS doesn't keep running an old version after an update.
        self.server_id = uuid.uuid4().hex[:8]

    def _message(self, state: dict) -> dict:
        return {**state, "server": self.server_id}

    async def connect(self, ws: WebSocket, state: dict) -> None:
        await ws.accept()
        self.clients.add(ws)
        await ws.send_json(self._message(state))

    def disconnect(self, ws: WebSocket) -> None:
        self.clients.discard(ws)

    async def broadcast(self, state: dict) -> None:
        msg = self._message(state)
        dead = []
        for ws in list(self.clients):
            try:
                await ws.send_json(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


# ---------------------------------------------------------------- app


def create_app(state_path: Path | str = DEFAULT_STATE_PATH) -> FastAPI:
    app = FastAPI(title="MTG Token Overlay")
    store = TokenStore(Path(state_path))
    presets = PresetStore(Path(state_path).with_name(Path(state_path).stem + "-presets.json"))
    decks = DeckStore(Path(state_path).with_name(Path(state_path).stem + "-deck.json"))
    hub = Hub()
    app.state.store = store
    app.state.hub = hub
    app.state.decks = decks
    # Set by the collection manager: result_hook(result, deck) logs the game of a saved deck and
    # returns {"logged": bool, "record": "5 W · 3 L", "note": "..."}.
    app.state.result_hook = None
    # tonight_hook() returns {"wins": 2, "losses": 1, "draws": 0}: tonight's score, from the game log.
    app.state.tonight_hook = None

    async def publish() -> dict:
        state = store.snapshot()
        await hub.broadcast(state)
        return state

    @app.get("/", include_in_schema=False)
    def index():
        return RedirectResponse("control")  # relative: works wherever the app is mounted

    # OBS (and browsers) must not serve the pages from cache; otherwise you'd
    # still see the old version after an update.
    no_cache = {"Cache-Control": "no-store"}

    @app.get("/control", include_in_schema=False)
    def control_page():
        return FileResponse(STATIC_DIR / "control.html", headers=no_cache)

    @app.get("/overlay", include_in_schema=False)
    def overlay_page():
        return FileResponse(STATIC_DIR / "overlay.html", headers=no_cache)

    running_code = code_fingerprint()

    @app.get("/api/version")
    def version():
        # The pages are read from disk on every load, but main.py only when the
        # server starts. If it changed since, the server needs a restart.
        on_disk = code_fingerprint()
        return {"running": running_code, "on_disk": on_disk, "outdated": on_disk != running_code}

    @app.get("/api/state")
    def get_state():
        return store.snapshot()

    @app.post("/api/tokens", status_code=201)
    async def add_token(item: TokenIn):
        try:
            store.add(item)
        except OverflowError:
            raise HTTPException(409, f"at most {MAX_TOKENS} stacks")
        return await publish()

    @app.get("/api/abilities")
    def abilities():
        return ABILITIES

    @app.get("/api/counter-types")
    def counter_types():
        return {"pt": list(PT_COUNTERS), "keywords": list(KEYWORD_COUNTERS), "mana": list(MANA_COUNTERS)}

    @app.post("/api/counters", status_code=201)
    async def add_counter(body: CounterIn):
        try:
            store.add_counter(body.type, body.value)
        except OverflowError:
            raise HTTPException(409, f"at most {MAX_TOKENS} stacks")
        return await publish()

    @app.patch("/api/tokens/{token_id}")
    async def change_token(token_id: str, body: TokenPatch):
        try:
            store.update(token_id, body)
        except KeyError:
            raise HTTPException(404, "token not found")
        return await publish()

    @app.post("/api/tokens/{token_id}/split")
    async def split_token(token_id: str, body: SplitIn):
        try:
            store.split(token_id, body.count)
        except KeyError:
            raise HTTPException(404, "token not found")
        except ValueError:
            raise HTTPException(422, "you can only split off fewer tokens than the stack holds")
        except OverflowError:
            raise HTTPException(409, f"at most {MAX_TOKENS} stacks")
        return await publish()

    @app.post("/api/tokens/{token_id}/use")
    async def use_token(token_id: str, body: UseIn | None = None):
        try:
            token, ability = store.use(token_id, body.color if body else None)
        except KeyError:
            raise HTTPException(404, "token not found")
        except TypeError:
            raise HTTPException(422, "this token has no ability to use")
        except PermissionError:
            raise HTTPException(409, "already tapped")
        except OverflowError:
            raise HTTPException(409, f"at most {MAX_TOKENS} stacks")
        # Like sacrifice: the event first, so the overlay can animate the card.
        event = {"type": "use", "id": token_id, "name": token.name,
                 "tap": ability["tap"], "sac": ability["sac"], "text": ability["text"]}
        await hub.broadcast({"event": event})
        return await publish()

    @app.post("/api/tokens/{token_id}/sacrifice")
    async def sacrifice_token(token_id: str, body: SplitIn):
        try:
            token, n = store.sacrifice(token_id, body.count)
        except KeyError:
            raise HTTPException(404, "token not found")
        except TypeError:
            raise HTTPException(422, "only tokens and cards can be sacrificed, not counters")
        # The event goes out before the new state, so the overlay can play the
        # sacrifice effect on the card while it's still there.
        event = {"type": "sacrifice", "id": token_id, "name": token.name, "count": n}
        await hub.broadcast({"event": event})
        return await publish()

    @app.post("/api/tokens/{token_id}/merge")
    async def merge_token(token_id: str):
        try:
            store.merge(token_id)
        except KeyError:
            raise HTTPException(404, "token not found")
        return await publish()

    @app.post("/api/positions/reset")
    async def reset_positions():
        store.reset_positions()
        return await publish()

    @app.post("/api/roll")
    async def roll(body: RollIn):
        # Rolled on the server so every page shows the same result; secrets
        # gives fair randomness. Not stored, just broadcast as an event.
        sides = ROLL_SIDES[body.kind]
        rolls = [secrets.randbelow(sides) + 1 for _ in range(body.count)]
        event = {"type": "roll", "id": uuid.uuid4().hex[:8], "kind": body.kind}
        if body.kind == "coin":
            event["results"] = ["heads" if r == 1 else "tails" for r in rolls]
        else:
            event["results"] = rolls
            event["total"] = sum(rolls)
        await hub.broadcast({"event": event})
        return event

    @app.post("/api/mana/empty")
    async def empty_mana_pool():
        store.empty_mana_pool()
        return await publish()

    async def phase_event() -> None:
        await hub.broadcast({"event": {"type": "phase", **store.turn}})

    @app.post("/api/turn/next")
    async def next_phase():
        result = store.next_phase()
        if result["new_turn"]:
            await hub.broadcast({"event": {"type": "turn", "number": store.turn["number"]}})
            await hub.broadcast({"event": {"type": "untap", "count": result["untapped"]}})
        await phase_event()
        return await publish()

    @app.post("/api/turn/prev")
    async def prev_phase():
        store.prev_phase()
        await phase_event()
        return await publish()

    @app.patch("/api/turn")
    async def set_turn(body: TurnPatch):
        store.set_turn(body)
        await phase_event()
        return await publish()

    @app.post("/api/tally/{name}")
    async def tally(name: Literal["casts", "storm"], body: TallyIn | None = None):
        """Commander cast (casts) or a spell for storm (storm): +1 by default, {"delta": -1} takes one back."""
        delta = body.delta if body else 1
        value = store.tally(name, delta)
        await hub.broadcast({"event": {"type": "tally", "name": name, "value": value, "delta": delta}})
        return await publish()

    @app.post("/api/spotlight")
    async def spotlight(body: SpotlightIn):
        """Show one card big on stream for a few seconds. Not stored, just an event."""
        event = {"type": "spotlight", "id": uuid.uuid4().hex[:8], **body.model_dump()}
        await hub.broadcast({"event": event})
        return event

    @app.delete("/api/spotlight")
    async def end_spotlight():
        await hub.broadcast({"event": {"type": "spotlight", "id": None}})
        return None

    @app.post("/api/intro")
    async def intro(body: IntroIn | None = None):
        """The deck intro on stream: the one sent, or the one of the deck in the control panel."""
        if body is None:
            deck = decks.deck or {}
            data = deck.get("intro") or ({"name": deck["name"]} if deck.get("name") else None)
            if data is None:
                raise HTTPException(404, "no deck to introduce: pick a deck first")
            body = IntroIn(**data)
        event = {"type": "intro", **body.model_dump()}
        await hub.broadcast({"event": event})
        return event

    @app.post("/api/result")
    async def game_result(body: ResultIn):
        """The game is over: Victory!, GG or Draw on stream, and the game in the log."""
        deck = decks.deck or {}
        info = {}
        if body.log and app.state.result_hook is not None:
            info = await asyncio.to_thread(app.state.result_hook, body.result, deck) or {}
        event = {"type": "result", "result": body.result, "deck": deck.get("name"),
                 "record": info.get("record"), "logged": bool(info.get("logged")), "note": info.get("note"),
                 "tonight": info.get("tonight"), "achievements": info.get("achievements") or []}
        await hub.broadcast({"event": event})
        return event

    @app.get("/api/tonight")
    async def tonight_score():
        """Tonight's wins, losses and draws (from the game log of the collection manager)."""
        if app.state.tonight_hook is None:
            return {"wins": 0, "losses": 0, "draws": 0}
        return await asyncio.to_thread(app.state.tonight_hook)

    @app.post("/api/untap")
    async def untap_all():
        tapped = sum(1 for t in store.tokens if t.tapped)
        store.untap_all()
        # The untap step: the overlay shows a short banner.
        await hub.broadcast({"event": {"type": "untap", "count": tapped}})
        return await publish()

    @app.post("/api/tokens/{token_id}/attack")
    async def attack(token_id: str):
        try:
            token = store.attack(token_id)
        except KeyError:
            raise HTTPException(404, "token not found")
        except TypeError:
            raise HTTPException(422, "only creatures can attack")
        except PermissionError:
            raise HTTPException(409, "already tapped")
        await hub.broadcast({"event": {"type": "attack", "ids": [token_id], "names": [token.name]}})
        return await publish()

    @app.post("/api/attack-all")
    async def attack_all():
        ids = store.attack_all()
        if ids:
            await hub.broadcast({"event": {"type": "attack", "ids": ids}})
        return await publish()

    @app.post("/api/tokens/{token_id}/copy")
    async def copy_token(token_id: str, body: SplitIn):
        try:
            store.copy(token_id, body.count)
        except KeyError:
            raise HTTPException(404, "token not found")
        except TypeError:
            raise HTTPException(422, "only tokens and cards can be copied")
        except OverflowError:
            raise HTTPException(409, f"at most {MAX_TOKENS} stacks")
        return await publish()

    @app.post("/api/undo")
    async def undo():
        try:
            store.undo()
        except LookupError:
            raise HTTPException(409, "nothing to undo")
        return await publish()

    @app.get("/api/deck")
    def get_deck():
        return decks.deck

    @app.post("/api/deck")
    async def import_deck(body: DeckIn):
        try:
            # Network calls: run them off the event loop so the overlay keeps going.
            deck = await asyncio.to_thread(find_deck_tokens, body.url)
        except DeckError as e:
            raise HTTPException(e.status, e.message)
        decks.save(deck)
        await hub.broadcast({"event": {"type": "deck"}})   # other docks refresh
        return deck

    @app.delete("/api/deck")
    async def forget_deck():
        decks.save(None)
        await hub.broadcast({"event": {"type": "deck"}})
        return None

    @app.get("/api/presets")
    def list_presets():
        return presets.summary()

    @app.post("/api/presets", status_code=201)
    async def save_preset(body: PresetIn):
        try:
            presets.save(body.name, store.tokens)
        except OverflowError:
            raise HTTPException(409, f"at most {MAX_PRESETS} presets")
        await hub.broadcast({"event": {"type": "presets"}})
        return presets.summary()

    @app.post("/api/presets/{name}/load")
    async def load_preset(name: str):
        if name not in presets.presets:
            raise HTTPException(404, "preset not found")
        store.replace_all(presets.board(name))
        return await publish()

    @app.delete("/api/presets/{name}")
    async def delete_preset(name: str):
        if name not in presets.presets:
            raise HTTPException(404, "preset not found")
        presets.delete(name)
        await hub.broadcast({"event": {"type": "presets"}})
        return presets.summary()

    @app.delete("/api/tokens/{token_id}")
    async def delete_token(token_id: str):
        try:
            store.remove(token_id)
        except KeyError:
            raise HTTPException(404, "token not found")
        return await publish()

    @app.post("/api/clear")
    async def clear_tokens():
        store.clear()
        return await publish()

    # ---- Stream Deck

    @app.get("/streamdeck", include_in_schema=False)
    def streamdeck_page():
        return FileResponse(STATIC_DIR / "streamdeck.html", headers=no_cache)

    @app.get("/api/streamdeck")
    def streamdeck_actions():
        """The Stream Deck keys in order, and the presets that can have a key of their own."""
        return {"actions": [{"action": a, "label": label} for a, label in STREAMDECK_ACTIONS],
                "presets": [p["name"] for p in presets.summary()], "preset_keys": STREAMDECK_PRESET_KEYS}

    symbols: dict[str, bytes] = {}

    @app.get("/api/symbol/{letter}.svg", include_in_schema=False)
    def mana_symbol(letter: str):
        """A mana symbol from Scryfall, served from here so the Stream Deck page can draw it into a key picture."""
        if letter not in MANA_SYMBOLS:
            raise HTTPException(404, "unknown symbol")
        if letter not in symbols:
            req = urllib.request.Request(f"https://svgs.scryfall.io/card-symbols/{letter}.svg",
                                         headers={"User-Agent": USER_AGENT})
            try:
                with urllib.request.urlopen(req, timeout=10) as res:
                    symbols[letter] = res.read(200_000)
            except (urllib.error.URLError, OSError):
                raise HTTPException(502, "Scryfall can't be reached")
        return Response(symbols[letter], media_type="image/svg+xml", headers={"Cache-Control": "max-age=604800"})

    def from_elsewhere(request: Request) -> bool:
        """A web page on another site that tries to press a key for you (a Stream Deck sends neither header)."""
        host = request.headers.get("host", "")
        return any(urllib.parse.urlparse(value).netloc != host
                   for value in (request.headers.get("origin"), request.headers.get("referer")) if value)

    async def press(action: str):
        if action in {"mana-" + m for m in MANA_SYMBOLS}:
            return await add_counter(CounterIn(type=action[-1]))
        if action in {"d20", "d6", "coin", "2d6"}:
            return await roll(RollIn(kind="d6", count=2) if action == "2d6" else RollIn(kind=action))
        simple = {
            "next-phase": next_phase, "prev-phase": prev_phase, "untap": untap_all, "attack-all": attack_all,
            "undo": undo, "spotlight-hide": end_spotlight, "mana-empty": empty_mana_pool, "clear": clear_tokens,
        }
        if action in simple:
            return await simple[action]()
        if action == "intro":
            return await intro(None)
        if action == "new-game":
            return await set_turn(TurnPatch(on=True, number=1, phase="untap", casts=0, storm=0))
        if action in ("tracker-on", "tracker-off"):
            return await set_turn(TurnPatch(on=action == "tracker-on"))
        if action == "tracker":
            return await set_turn(TurnPatch(on=not store.turn["on"]))
        if action in ("win", "loss", "draw"):
            return await game_result(ResultIn(result=action))
        if action in ("commander", "commander-back", "storm", "storm-back"):
            name = "casts" if action.startswith("commander") else "storm"
            return await tally(name, TallyIn(delta=-1 if action.endswith("-back") else 1))
        if action in ("plus-counter", "minus-counter"):
            return await add_counter(CounterIn(type="+1/+1" if action == "plus-counter" else "-1/-1"))
        raise HTTPException(404, "unknown action")

    @app.get("/sd/preset/{name}")
    async def streamdeck_preset(name: str, request: Request):
        """Stream Deck: load a preset (a plain GET, for the Website action)."""
        if from_elsewhere(request):
            raise HTTPException(403, "only from your own Stream Deck or this app")
        await load_preset(name)
        return {"ok": True, "action": f"preset/{name}"}

    @app.get("/sd/{action}")
    async def streamdeck_press(action: str, request: Request):
        """Stream Deck: press one of the keys of STREAMDECK_ACTIONS (a plain GET, for the Website action)."""
        if from_elsewhere(request):
            raise HTTPException(403, "only from your own Stream Deck or this app")
        await press(action)
        return {"ok": True, "action": action}

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket):
        await hub.connect(ws, store.snapshot())
        try:
            while True:
                # Clients don't send anything meaningful; we only read to
                # notice a dropped connection.
                await ws.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            hub.disconnect(ws)

    return app



async def set_deck(app: FastAPI, deck: dict | None, intro: bool = False) -> None:
    """Show these deck tokens in the control panel (from the collection manager), as a scan would;
    with intro, the overlay also shows the deck intro."""
    app.state.decks.save(deck)
    await app.state.hub.broadcast({"event": {"type": "deck"}})
    if intro and deck and deck.get("intro"):
        await show_intro(app, deck["intro"])


async def show_intro(app: FastAPI, intro: dict) -> dict:
    event = {"type": "intro", **IntroIn(**intro).model_dump()}
    await app.state.hub.broadcast({"event": event})
    return event
