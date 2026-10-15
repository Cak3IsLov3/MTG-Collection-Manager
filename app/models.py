import datetime as dt

from sqlmodel import Field, SQLModel, UniqueConstraint


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


class Card(SQLModel, table=True):
    """One specific printing of a card, identified by its Scryfall id."""

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    set_code: str
    set_name: str
    scryfall_id: str = Field(unique=True, index=True)
    collector_number: str | None = None
    image_url: str | None = None
    # Colour identity as letters in WUBRG order; "" is colourless, None not fetched yet.
    colors: str | None = None


class CollectionEntry(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    card_id: int = Field(foreign_key="card.id", index=True)
    quantity: int = 1
    is_foil: bool = False
    added_at: dt.datetime = Field(default_factory=utcnow)
    # Came from a CSV import. Importing with "replace" only replaces these; cards added by hand stay.
    imported: bool = False
    # Copies from an import that wait for you to sort them into decks: the app does not hand
    # them out to decks on its own until you put them in a deck or let it decide.
    unsorted: int = 0


class WishlistEntry(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    card_id: int = Field(foreign_key="card.id", index=True)
    target_price: float
    cardmarket_url: str | None = None
    active: bool = True
    added_at: dt.datetime = Field(default_factory=utcnow)


class PriceSnapshot(SQLModel, table=True):
    """At most one price per card, per day, per source."""

    __table_args__ = (UniqueConstraint("card_id", "date", "source"),)

    id: int | None = Field(default=None, primary_key=True)
    card_id: int = Field(foreign_key="card.id", index=True)
    date: dt.date = Field(index=True)
    source: str  # "scryfall" | "cardmarket"
    price_eur: float | None = None
    price_eur_foil: float | None = None
    # Set code of another printing when this printing has no price of its own and the price is borrowed.
    eur_from_set: str | None = None
    eur_foil_from_set: str | None = None


class PriceRiseAlert(SQLModel, table=True):
    """A card you own that rose in price; sent to Discord once, after the daily price update."""

    id: int | None = Field(default=None, primary_key=True)
    card_id: int = Field(foreign_key="card.id", index=True)
    is_foil: bool = False
    sent_at: dt.datetime = Field(default_factory=utcnow)
    old_price: float
    new_price: float
    days: int  # compared with the price this many days earlier
    notified: bool = False  # the Discord message went out


class Game(SQLModel, table=True):
    """One game played with one of your decks."""

    id: int | None = Field(default=None, primary_key=True)
    played_on: dt.date = Field(index=True)
    deck_id: int | None = Field(default=None, index=True)  # None once the deck is deleted
    deck_name: str  # kept, so the log still reads well after a deck is renamed or deleted
    result: str = "loss"  # "win" | "loss" | "draw"
    players: int | None = None
    notes: str | None = None
    created_at: dt.datetime = Field(default_factory=utcnow)


class Alert(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    wishlist_entry_id: int = Field(foreign_key="wishlistentry.id", index=True)
    sent_at: dt.datetime = Field(default_factory=utcnow)
    price_at_alert: float


class DailyTotal(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    date: dt.date = Field(unique=True, index=True)
    collection_value: float = 0.0
    wishlist_cost: float = 0.0


class Deck(SQLModel, table=True):
    """A saved deck. Older decks get first pick of the cards in the collection."""

    id: int | None = Field(default=None, primary_key=True)
    name: str
    created_at: dt.datetime = Field(default_factory=utcnow)
    bracket: int | None = None  # Commander bracket 1-5, if known
    # The Archidekt deck this one follows: changes made there are taken over here.
    archidekt_id: int | None = None
    synced_at: dt.datetime | None = None
    # Colour identity of its cards, letters in WUBRG order; None: to be worked out (again).
    colors: str | None = None
    # Whether the deck uses tokens, as you set it; None: it does when its deck list has tokens.
    uses_tokens: bool | None = None
    # The commander: a card name from the deck. None: not known yet (worked out from the deck list
    # or the deck name); "": you chose none.
    commander: str | None = None
    commander_image: str | None = None  # Scryfall image of the commander


class DeckEntry(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    deck_id: int = Field(foreign_key="deck.id", index=True)
    name: str
    quantity: int = 1
    # True when the user took their copies out of the deck: the card is still on the list, but missing.
    unassigned: bool = False
    # The printing the deck list asks for (e.g. from Archidekt), when it names one.
    set_code: str | None = None
    collector_number: str | None = None
    scryfall_id: str | None = None
    foil: bool | None = None


class DeckBasicLand(SQLModel, table=True):
    """How many of a basic land a deck runs. Counted by name only: any printing will do."""

    id: int | None = Field(default=None, primary_key=True)
    deck_id: int = Field(foreign_key="deck.id", index=True)
    name: str  # "Plains", "Snow-Covered Island", ...
    quantity: int = 0


class DeckSkipped(SQLModel, table=True):
    """How many tokens were left out of a saved deck."""

    id: int | None = Field(default=None, primary_key=True)
    deck_id: int = Field(foreign_key="deck.id", index=True)
    kind: str  # "basic" | "token"
    quantity: int = 0


class DeckPick(SQLModel, table=True):
    """The printing the user chose for a card in a deck; without one the app picks."""

    id: int | None = Field(default=None, primary_key=True)
    deck_entry_id: int = Field(foreign_key="deckentry.id", unique=True, index=True)
    collection_entry_id: int = Field(foreign_key="collectionentry.id", index=True)
