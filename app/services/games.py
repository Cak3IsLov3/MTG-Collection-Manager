"""The game log: which deck you played, when, and whether you won."""

import datetime as dt
from dataclasses import dataclass

from sqlmodel import Session, select

from app.models import Deck, Game

RESULTS = {"win": "Won", "loss": "Lost", "draw": "Draw"}


class GameError(ValueError):
    pass


@dataclass
class DeckPlays:
    """How often a deck was played and how it went."""

    played: int = 0
    wins: int = 0
    losses: int = 0
    draws: int = 0
    last_played: dt.date | None = None

    @property
    def win_rate(self) -> int | None:
        return round(self.wins / self.played * 100) if self.played else None

    def days_since(self, today: dt.date | None = None) -> int | None:
        if self.last_played is None:
            return None
        return ((today or dt.date.today()) - self.last_played).days


def log_game(
    session: Session,
    deck: Deck,
    result: str,
    played_on: dt.date | None = None,
    players: int | None = None,
    notes: str | None = None,
) -> Game:
    if result not in RESULTS:
        raise GameError("A game is a win, a loss or a draw")
    if players is not None and not 1 <= players <= 12:
        raise GameError("Fill in between 1 and 12 players")
    played_on = played_on or dt.date.today()
    if played_on > dt.date.today():
        raise GameError("That date is still to come")
    game = Game(
        played_on=played_on, deck_id=deck.id, deck_name=deck.name, result=result, players=players,
        notes=(notes or "").strip() or None,
    )
    session.add(game)
    session.flush()
    return game


def delete_game(session: Session, game_id: int) -> None:
    game = session.get(Game, game_id)
    if game is None:
        raise GameError("That game is not in the log")
    session.delete(game)
    session.flush()


def forget_deck(session: Session, deck_id: int) -> None:
    """A deck is deleted: its games stay in the log under the name it had."""
    for game in session.exec(select(Game).where(Game.deck_id == deck_id)).all():
        game.deck_id = None
        session.add(game)
    session.flush()


def recent_games(session: Session, limit: int | None = None, deck_id: int | None = None) -> list[Game]:
    statement = select(Game).order_by(Game.played_on.desc(), Game.id.desc())
    if deck_id is not None:
        statement = statement.where(Game.deck_id == deck_id)
    if limit:
        statement = statement.limit(limit)
    return list(session.exec(statement))


def deck_plays(session: Session) -> dict[int, DeckPlays]:
    """Per saved deck: games played, wins, losses, draws and the last time it was played."""
    plays: dict[int, DeckPlays] = {}
    for game in session.exec(select(Game).where(Game.deck_id != None)):  # noqa: E711
        stats = plays.setdefault(game.deck_id, DeckPlays())
        stats.played += 1
        stats.wins += game.result == "win"
        stats.losses += game.result == "loss"
        stats.draws += game.result == "draw"
        if stats.last_played is None or game.played_on > stats.last_played:
            stats.last_played = game.played_on
    return plays
