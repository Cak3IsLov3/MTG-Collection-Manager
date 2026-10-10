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


class CollectionEntry(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    card_id: int = Field(foreign_key="card.id", index=True)
    quantity: int = 1
    is_foil: bool = False
    added_at: dt.datetime = Field(default_factory=utcnow)


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


class DeckEntry(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    deck_id: int = Field(foreign_key="deck.id", index=True)
    name: str
    quantity: int = 1
    # True when the user took their copies out of the deck: the card is still on the list, but missing.
    unassigned: bool = False


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
