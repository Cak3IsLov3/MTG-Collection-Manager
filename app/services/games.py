"""The game log: which deck you played, when, and whether you won."""

import datetime as dt
from dataclasses import dataclass, field

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


def split_names(text: str | None) -> list[str]:
    """"Sam, robin,  Sam" -> ["Sam", "robin"]: names separated by commas, each once."""
    names: dict[str, str] = {}
    for part in (text or "").split(","):
        name = " ".join(part.split())
        if name:
            names.setdefault(name.casefold(), name)
    return list(names.values())


def log_game(
    session: Session,
    deck: Deck,
    result: str,
    played_on: dt.date | None = None,
    players: int | None = None,
    notes: str | None = None,
    opponents: str | list[str] | None = None,
    winner: str | None = None,
) -> Game:
    if result not in RESULTS:
        raise GameError("A game is a win, a loss or a draw")
    if players is not None and not 1 <= players <= 12:
        raise GameError("Fill in between 1 and 12 players")
    played_on = played_on or dt.date.today()
    if played_on > dt.date.today():
        raise GameError("That date is still to come")
    names = split_names(", ".join(opponents) if isinstance(opponents, list) else opponents)
    # Only a lost game has another winner; that player was at the table too.
    winner = " ".join((winner or "").split()) if result == "loss" else ""
    if winner:
        known = {name.casefold(): name for name in names}
        winner = known.get(winner.casefold(), winner)
        if winner.casefold() not in known:
            names.append(winner)
    if players is not None and len(names) >= players:
        raise GameError(f"{len(names)} opponents do not fit at a table of {players}")
    game = Game(
        played_on=played_on, deck_id=deck.id, deck_name=deck.name, result=result, players=players,
        notes=(notes or "").strip() or None, opponents=", ".join(names) or None, winner=winner or None,
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


def known_players(session: Session) -> list[str]:
    """Everyone you played against, most often first (for suggestions in the form)."""
    counts: dict[str, list] = {}
    for game in session.exec(select(Game).where(Game.opponents != None)):  # noqa: E711
        for name in split_names(game.opponents):
            entry = counts.setdefault(name.casefold(), [name, 0])
            entry[1] += 1
    return [name for name, _ in sorted(counts.values(), key=lambda item: (-item[1], item[0].casefold()))]


@dataclass
class Tally:
    """Games and wins of one group: a month, a table size, a colour, an opponent."""

    label: str
    played: int = 0
    wins: int = 0
    their_wins: int = 0  # for an opponent: games they won against you

    @property
    def win_rate(self) -> int | None:
        return round(self.wins / self.played * 100) if self.played else None


@dataclass
class LogStats:
    played: int = 0
    wins: int = 0
    losses: int = 0
    draws: int = 0
    streak: tuple[str, int] | None = None  # the current run: ("win", 3)
    longest_win_streak: int = 0
    months: list[Tally] = field(default_factory=list)  # oldest first, the last twelve
    table_sizes: list[Tally] = field(default_factory=list)  # label "4": games with four players
    colours: list[Tally] = field(default_factory=list)  # label "W": games with a deck that plays white
    brackets: list[Tally] = field(default_factory=list)  # label "3": games with a bracket 3 deck
    opponents: list[Tally] = field(default_factory=list)  # most games first

    @property
    def win_rate(self) -> int | None:
        return round(self.wins / self.played * 100) if self.played else None

    @property
    def nemesis(self) -> Tally | None:
        """The player who beat you most often (at least twice)."""
        best = max(self.opponents, key=lambda tally: (tally.their_wins, -tally.wins), default=None)
        return best if best and best.their_wins >= 2 else None


def log_stats(session: Session, today: dt.date | None = None) -> LogStats:
    """Win rates over the whole log: per month, table size, deck colour, bracket and opponent."""
    today = today or dt.date.today()
    games = list(session.exec(select(Game).order_by(Game.played_on, Game.id)))
    stats = LogStats()
    if not games:
        return stats
    decks = {deck.id: deck for deck in session.exec(select(Deck))}

    def count(groups: dict[str, Tally], label: str, game: Game) -> Tally:
        tally = groups.setdefault(label, Tally(label))
        tally.played += 1
        tally.wins += game.result == "win"
        return tally

    months: dict[str, Tally] = {}
    sizes: dict[str, Tally] = {}
    colours: dict[str, Tally] = {}
    brackets: dict[str, Tally] = {}
    opponents: dict[str, Tally] = {}
    run = 0
    for game in games:
        stats.played += 1
        stats.wins += game.result == "win"
        stats.losses += game.result == "loss"
        stats.draws += game.result == "draw"
        if stats.streak and stats.streak[0] == game.result:
            stats.streak = (game.result, stats.streak[1] + 1)
        else:
            stats.streak = (game.result, 1)
        run = run + 1 if game.result == "win" else 0
        stats.longest_win_streak = max(stats.longest_win_streak, run)
        count(months, game.played_on.strftime("%Y-%m"), game)
        if game.players:
            count(sizes, str(game.players), game)
        deck = decks.get(game.deck_id)
        if deck is not None:
            for letter in deck.colors or ("C" if deck.colors == "" else ""):
                count(colours, letter, game)
            if deck.bracket:
                count(brackets, str(deck.bracket), game)
        for name in split_names(game.opponents):
            key = name.casefold()
            tally = opponents.get(key) or opponents.setdefault(key, Tally(name))
            tally.played += 1
            tally.wins += game.result == "win"
            tally.their_wins += (game.winner or "").casefold() == key

    # Every month from the first game on, also the ones without games; the last twelve.
    first = games[0].played_on.replace(day=1)
    month = max(first, (today.replace(day=1) - dt.timedelta(days=335)).replace(day=1))
    while month <= today:
        label = month.strftime("%Y-%m")
        stats.months.append(months.get(label, Tally(label)))
        month = (month + dt.timedelta(days=32)).replace(day=1)
    stats.table_sizes = sorted(sizes.values(), key=lambda tally: int(tally.label))
    stats.colours = [colours[letter] for letter in "WUBRGC" if letter in colours]
    stats.brackets = sorted(brackets.values(), key=lambda tally: int(tally.label))
    stats.opponents = sorted(opponents.values(), key=lambda tally: (-tally.played, tally.label.casefold()))
    return stats


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
