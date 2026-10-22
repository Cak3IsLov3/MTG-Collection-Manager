"""Achievements, worked out from the game log: nothing extra is stored, so logging, changing or deleting
a game is always reflected."""

import datetime as dt
from dataclasses import dataclass

from sqlmodel import Session, select

from app.models import Deck, Game


@dataclass
class Achievement:
    key: str
    icon: str  # a name from app.icons
    title: str
    text: str
    goal: int = 1
    progress: int = 0
    unlocked_on: dt.date | None = None
    deck_id: int | None = None

    @property
    def unlocked(self) -> bool:
        return self.unlocked_on is not None


# (key, icon, title, text, goal); the progress of each is counted below.
GLOBAL = (
    ("first-win", "medal", "First blood", "Win a game", 1),
    ("wins-10", "count-10", "Ten wins", "Win 10 games", 10),
    ("wins-25", "count-25", "Twenty-five wins", "Win 25 games", 25),
    ("wins-50", "count-50", "Fifty wins", "Win 50 games", 50),
    ("wins-100", "crown", "Centurion", "Win 100 games", 100),
    ("streak-3", "flame", "On a roll", "Win 3 games in a row", 3),
    ("streak-5", "bolt", "Unstoppable", "Win 5 games in a row", 5),
    ("nights-5", "moon", "Regular", "Play on 5 game nights", 5),
    ("nights-25", "castle", "Veteran", "Play on 25 game nights", 25),
    ("sweep", "broom", "Clean sweep", "Win every game of a night, at least 3", 1),
    ("big-table", "users", "Big table", "Win at a table of 5 or more", 1),
    ("high-power", "gem", "High power", "Win with a bracket 4 or 5 deck", 1),
    ("variety", "dice", "Variety", "Win with 5 different decks", 5),
    ("rainbow", "rainbow", "All five colours", "Win with decks that together have W, U, B, R and G", 5),
)
PER_DECK = (
    ("first", "medal", "First win", "First win with {deck}", 1),
    ("ten", "count-10", "Ten wins", "Ten wins with {deck}", 10),
    ("hat-trick", "tophat", "Hat-trick", "Three wins in a row with {deck}", 3),
)


def _games(session: Session) -> list[Game]:
    return sorted(session.exec(select(Game)), key=lambda g: (g.played_on, g.id or 0))


def achievements(session: Session) -> tuple[list[Achievement], dict[int, list[Achievement]]]:
    """All achievements (unlocked or not, with progress), and per deck the ones of that deck."""
    decks = {deck.id: deck for deck in session.exec(select(Deck))}
    found = {key: Achievement(key, icon, title, text, goal) for key, icon, title, text, goal in GLOBAL}
    per_deck: dict[int, dict[str, Achievement]] = {}

    def reach(item: Achievement, value: int, day: dt.date) -> None:
        item.progress = max(item.progress, min(value, item.goal))
        if value >= item.goal and item.unlocked_on is None:
            item.unlocked_on = day

    wins = streak = 0
    nights: dict[dt.date, list[str]] = {}
    winning_decks: set[int] = set()
    colours: set[str] = set()
    deck_wins: dict[int, int] = {}
    deck_streak: dict[int, int] = {}
    for game in _games(session):
        day = game.played_on
        nights.setdefault(day, []).append(game.result)
        reach(found["nights-5"], len(nights), day)
        reach(found["nights-25"], len(nights), day)
        deck = decks.get(game.deck_id)
        if deck is not None and deck.id not in per_deck:
            per_deck[deck.id] = {key: Achievement(f"deck-{deck.id}-{key}", icon, title, text.format(deck=deck.name), goal, deck_id=deck.id)
                                 for key, icon, title, text, goal in PER_DECK}
        if game.result != "win":
            streak = 0
            if deck is not None:
                deck_streak[deck.id] = 0
            continue
        wins += 1
        streak += 1
        for key in ("first-win", "wins-10", "wins-25", "wins-50", "wins-100"):
            reach(found[key], wins, day)
        reach(found["streak-3"], streak, day)
        reach(found["streak-5"], streak, day)
        if (game.players or 0) >= 5:
            reach(found["big-table"], 1, day)
        results = nights[day]
        if len(results) >= 3 and all(result == "win" for result in results):
            reach(found["sweep"], 1, day)
        if deck is None:
            continue
        if (deck.bracket or 0) >= 4:
            reach(found["high-power"], 1, day)
        winning_decks.add(deck.id)
        reach(found["variety"], len(winning_decks), day)
        colours.update(deck.colors or "")
        reach(found["rainbow"], len(colours & set("WUBRG")), day)
        deck_wins[deck.id] = deck_wins.get(deck.id, 0) + 1
        deck_streak[deck.id] = deck_streak.get(deck.id, 0) + 1
        mine = per_deck[deck.id]
        reach(mine["first"], deck_wins[deck.id], day)
        reach(mine["ten"], deck_wins[deck.id], day)
        reach(mine["hat-trick"], deck_streak[deck.id], day)
    return list(found.values()), {deck_id: list(items.values()) for deck_id, items in per_deck.items()}


def unlocked_keys(session: Session) -> set[str]:
    overall, per_deck = achievements(session)
    return {a.key for a in overall if a.unlocked} | {a.key for items in per_deck.values() for a in items if a.unlocked}


def newly_unlocked(session: Session, before: set[str]) -> list[Achievement]:
    """The achievements unlocked since `before` (keys from unlocked_keys), e.g. by the game just logged."""
    overall, per_deck = achievements(session)
    every = overall + [a for items in per_deck.values() for a in items]
    return [a for a in every if a.unlocked and a.key not in before]
