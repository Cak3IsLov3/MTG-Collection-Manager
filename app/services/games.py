"""The game log: which deck you played, when, and whether you won."""

import datetime as dt
import json
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

    def played_tonight(self, now: dt.datetime | None = None) -> bool:
        return self.last_played is not None and self.last_played >= tonight(now)


def tonight(now: dt.datetime | None = None) -> dt.date:
    """The date of tonight's game night: until six in the morning that is still yesterday."""
    now = now or dt.datetime.now()
    return (now - dt.timedelta(hours=6)).date()


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
    opponent_commanders: dict[str, str] | None = None,
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
    # The commander per opponent, under the name as it is in the list of opponents.
    known = {name.casefold(): name for name in names}
    commanders = {}
    for name, commander in (opponent_commanders or {}).items():
        name, commander = " ".join(name.split()), " ".join((commander or "").split())
        if commander and name.casefold() in known:
            commanders[known[name.casefold()]] = commander
    game = Game(
        played_on=played_on, deck_id=deck.id, deck_name=deck.name, result=result, players=players,
        notes=(notes or "").strip() or None, opponents=", ".join(names) or None, winner=winner or None,
        opponent_commanders=json.dumps(commanders) if commanders else None,
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


def game_commanders(game: Game) -> dict[str, str]:
    """{opponent: commander} of a game."""
    return json.loads(game.opponent_commanders) if game.opponent_commanders else {}


def last_commanders(session: Session) -> dict[str, str]:
    """The commander every player used the last time (to fill in for the next game)."""
    last: dict[str, str] = {}
    for game in session.exec(select(Game).where(Game.opponent_commanders != None).order_by(Game.played_on, Game.id)):  # noqa: E711
        last.update(game_commanders(game))
    return last


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
    decks: list[str] = field(default_factory=list)  # for an opponent: the commanders they played

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
    commanders: list[Tally] = field(default_factory=list)  # the commanders you played against, most games first

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
    commanders: dict[str, Tally] = {}
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
        played_with = {name.casefold(): commander for name, commander in game_commanders(game).items()}
        for name in split_names(game.opponents):
            key = name.casefold()
            tally = opponents.get(key) or opponents.setdefault(key, Tally(name))
            tally.played += 1
            tally.wins += game.result == "win"
            won = (game.winner or "").casefold() == key
            tally.their_wins += won
            commander = played_with.get(key)
            if commander:
                if commander not in tally.decks:
                    tally.decks.append(commander)
                against = commanders.get(commander.casefold()) or commanders.setdefault(commander.casefold(), Tally(commander))
                against.played += 1
                against.wins += game.result == "win"
                against.their_wins += won

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
    stats.commanders = sorted(commanders.values(), key=lambda tally: (-tally.played, -tally.their_wins, tally.label.casefold()))
    return stats


@dataclass
class GameNight:
    """All the games of one day."""

    night: dt.date
    games: list[Game] = field(default_factory=list)
    winners: dict[str, int] = field(default_factory=dict)  # "You", "Sam" -> games won
    decks: dict[str, int] = field(default_factory=dict)  # your decks played -> games
    commanders: dict[str, int] = field(default_factory=dict)  # commanders you played against -> games
    players: list[str] = field(default_factory=list)  # who else was there
    sent: bool = False  # the recap went to Discord

    @property
    def wins(self) -> int:
        return sum(game.result == "win" for game in self.games)

    def lines(self, deck_names: dict[int, str] | None = None) -> list[str]:
        """The night in short, for Discord."""
        played = len(self.games)
        lines = [f"**{played} game{'s' if played != 1 else ''}**, you won {self.wins}"]
        if self.players:
            lines.append(f"**At the table** {', '.join(self.players)}")
        if self.winners:
            lines.append("**Wins** " + " · ".join(f"{name} {count}×" for name, count in self.winners.items()))
        if self.decks:
            lines.append("**Your decks** " + " · ".join(f"{name} {count}×" for name, count in self.decks.items()))
        if self.commanders:
            lines.append("**Against** " + " · ".join(f"{name} {count}×" for name, count in self.commanders.items()))
        notes = [game.notes for game in self.games if game.notes]
        if notes:
            lines.append("**Notes** " + " · ".join(notes[:5]))
        return lines


def game_nights(session: Session, limit: int | None = None) -> list[GameNight]:
    """The game nights (days with games), newest first, with who won and what was played."""
    from app.models import GameNightRecap

    sent = set(session.exec(select(GameNightRecap.night)).all())
    names = {deck.id: deck.name for deck in session.exec(select(Deck))}
    nights: dict[dt.date, GameNight] = {}
    for game in session.exec(select(Game).order_by(Game.played_on.desc(), Game.id)):
        night = nights.get(game.played_on) or nights.setdefault(game.played_on, GameNight(game.played_on, sent=game.played_on in sent))
        night.games.append(game)
        winner = "You" if game.result == "win" else game.winner
        if winner:
            night.winners[winner] = night.winners.get(winner, 0) + 1
        deck_name = names.get(game.deck_id, game.deck_name)
        night.decks[deck_name] = night.decks.get(deck_name, 0) + 1
        for name in split_names(game.opponents):
            if name.casefold() not in {p.casefold() for p in night.players}:
                night.players.append(name)
        for commander in game_commanders(game).values():
            night.commanders[commander] = night.commanders.get(commander, 0) + 1
    result = list(nights.values())
    for night in result:
        night.winners = dict(sorted(night.winners.items(), key=lambda item: -item[1]))
        night.decks = dict(sorted(night.decks.items(), key=lambda item: -item[1]))
        night.commanders = dict(sorted(night.commanders.items(), key=lambda item: -item[1]))
    return result[:limit] if limit else result


def mark_recap_sent(session: Session, night: dt.date) -> None:
    from app.models import GameNightRecap

    if session.exec(select(GameNightRecap).where(GameNightRecap.night == night)).first() is None:
        session.add(GameNightRecap(night=night))
        session.flush()


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
