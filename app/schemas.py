import datetime as dt

from pydantic import BaseModel, ConfigDict, Field

from app.services.collection import CollectionRow
from app.services.wishlist import WishlistRow


class CardOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    set_code: str
    set_name: str
    scryfall_id: str
    collector_number: str | None
    image_url: str | None
    # Colour identity as letters in WUBRG order; "" is colourless, null not fetched yet.
    colors: str | None = None


class CollectionAdd(BaseModel):
    # Either a name (optionally with a set), or a set + collector number for one exact printing.
    name: str | None = None
    set_code: str | None = None
    collector_number: str | None = None
    quantity: int = Field(default=1, ge=1)
    is_foil: bool = False


class CollectionUpdate(BaseModel):
    # Send what should change: the number of copies, the finish, or both.
    quantity: int | None = Field(default=None, ge=1)
    is_foil: bool | None = None


class DeckUseOut(BaseModel):
    id: int
    name: str
    quantity: int  # copies of this collection entry the deck uses


class DeckNeedOut(BaseModel):
    id: int
    name: str
    quantity: int  # copies the deck misses
    entry_id: int  # the card in that deck, e.g. for /api/decks/{id}/cards/{entry_id}/new-copy


class CollectionItemOut(BaseModel):
    id: int
    card: CardOut
    quantity: int
    is_foil: bool
    added_at: dt.datetime
    unit_price: float | None
    total_price: float | None
    # Set code when the printing has no price of its own and another printing's price is used.
    price_from_set: str | None = None
    # Saved decks these copies are set aside for (filled in by the collection list).
    in_decks: list[DeckUseOut] = []
    # Copies that are in no saved deck (filled in by the collection list).
    free: int | None = None
    # Saved decks that still miss this card while a copy of it is free (quantity: copies the deck misses,
    # entry_id: the card in that deck).
    wanted_by: list[DeckNeedOut] = []
    # New copies from an import: no deck gets them until you put them in one (or let the app decide).
    waiting: int = 0
    # Copies that no deck uses or needs (basic lands not counted): to trade or sell.
    spare: int = 0

    @classmethod
    def from_row(cls, row: CollectionRow) -> "CollectionItemOut":
        return cls(
            id=row.entry.id,
            card=CardOut.model_validate(row.card),
            quantity=row.entry.quantity,
            is_foil=row.entry.is_foil,
            added_at=row.entry.added_at,
            unit_price=row.unit_price,
            total_price=row.total_price,
            price_from_set=row.price_from_set,
        )


class CollectionOut(BaseModel):
    total_value: float
    items: list[CollectionItemOut]


class ImportOut(BaseModel):
    format: str
    imported_rows: int
    imported_cards: int
    not_found: list[str]
    errors: list[str]
    # With replace=true: copies you added by hand that the file does not have; they were kept.
    kept_manual: int = 0
    # Copies that are new in the collection; they wait for you to put them in decks.
    new_copies: int = 0


class WishlistAdd(BaseModel):
    name: str = Field(min_length=1)
    target_price: float = Field(ge=0)
    set_code: str | None = None
    cardmarket_url: str | None = None


class WishlistItemOut(BaseModel):
    id: int
    card: CardOut
    target_price: float
    cardmarket_url: str | None
    active: bool
    added_at: dt.datetime
    current_price: float | None
    price_source: str | None
    price_from_set: str | None = None
    below_target: bool

    @classmethod
    def from_row(cls, row: WishlistRow) -> "WishlistItemOut":
        return cls(
            id=row.entry.id,
            card=CardOut.model_validate(row.card),
            target_price=row.entry.target_price,
            cardmarket_url=row.entry.cardmarket_url,
            active=row.entry.active,
            added_at=row.entry.added_at,
            current_price=row.current_price,
            price_source=row.price_source,
            price_from_set=row.price_from_set,
            below_target=row.below_target,
        )


class PrintingOut(BaseModel):
    scryfall_id: str
    set_code: str
    set_name: str
    collector_number: str | None
    image_url: str | None
    price_eur: float | None
    price_eur_foil: float | None


class WishlistPrintingIn(BaseModel):
    scryfall_id: str = Field(min_length=1)


class WishlistOut(BaseModel):
    total_cost: float
    items: list[WishlistItemOut]


class PurchaseIn(BaseModel):
    quantity: int = Field(default=1, ge=1)
    is_foil: bool = False


class AlertOut(BaseModel):
    id: int
    card_name: str
    sent_at: dt.datetime
    price_at_alert: float
    target_price: float


class SummaryOut(BaseModel):
    collection_value: float
    wishlist_cost: float
    card_count: int
    unique_count: int
    wishlist_count: int
    wishlist_hits: int


class DeckCheckIn(BaseModel):
    decklist: str | None = None
    archidekt_url: str | None = None
    # Basic lands and tokens are left out of a deck unless asked for.
    include_basic_lands: bool = False
    include_tokens: bool = False


class DeckPickOut(BaseModel):
    quantity: int
    printing: CollectionItemOut


class DeckCardAdd(BaseModel):
    # Either a name (optionally with a set), or a set + collector number, like adding to the collection.
    name: str | None = None
    set_code: str | None = None
    collector_number: str | None = None
    quantity: int = Field(default=1, ge=1)


class DeckAssignedIn(BaseModel):
    # False takes your copies out of the deck (the card then counts as missing); True puts them back.
    assigned: bool


class DeckPrintingIn(BaseModel):
    # A collection entry id, or null to let the app choose again.
    collection_entry_id: int | None = None


class DeckCardOut(BaseModel):
    entry_id: int | None = None
    name: str
    needed: int
    owned: int
    reserved: int
    available: int
    missing: int
    on_wishlist: bool
    used_by: list[str] = []
    value: float = 0.0
    unassigned: bool = False
    # New copies from an import that wait for you; POST .../cards/{entry_id}/new-copy puts them in this deck.
    waiting: int = 0
    # The printing the deck list asks for (e.g. from Archidekt), if it names one.
    wanted_set_code: str | None = None
    wanted_collector_number: str | None = None
    wanted_foil: bool | None = None
    uses_wanted_printing: bool = False
    chosen_collection_entry_id: int | None = None
    using: list[DeckPickOut] = []
    printings: list[CollectionItemOut] = []

    @classmethod
    def from_row(cls, row) -> "DeckCardOut":
        return cls(
            name=row.name, needed=row.needed, owned=row.owned, reserved=row.reserved,
            available=row.available, missing=row.missing, on_wishlist=row.on_wishlist,
            used_by=row.used_by, printings=[CollectionItemOut.from_row(p) for p in row.printings],
            entry_id=row.entry_id, value=row.value, chosen_collection_entry_id=row.pinned_id,
            unassigned=row.unassigned,
            waiting=row.waiting,
            wanted_set_code=row.set_code, wanted_collector_number=row.collector_number, wanted_foil=row.foil,
            uses_wanted_printing=row.uses_wanted_printing,
            using=[DeckPickOut(quantity=n, printing=CollectionItemOut.from_row(p)) for p, n in row.picks],
        )


class DeckSaveIn(DeckCheckIn):
    name: str = Field(min_length=1)
    # Commander bracket 1-5; left out, an Archidekt deck keeps the bracket it has there.
    bracket: int | None = Field(default=None, ge=1, le=5)


class DeckBracketIn(BaseModel):
    bracket: int | None = Field(default=None, ge=1, le=5)


class DeckOut(BaseModel):
    id: int
    name: str
    cards_needed: int
    cards_usable: int
    complete: bool
    value: float
    bracket: int | None = None
    bracket_name: str | None = None
    basic_land_count: int = 0
    # Deck size including basic lands (a Commander deck has 100), and how many of those you have.
    deck_size: int = 0
    deck_owned: int = 0
    # The Archidekt deck this deck follows, and when it was last brought up to date.
    archidekt_url: str | None = None
    synced_at: dt.datetime | None = None
    # Colour identity of the deck's cards, letters in WUBRG order; null until it is worked out.
    colors: str | None = None


class DeckLinkIn(BaseModel):
    # An Archidekt deck URL, or null to stop following one.
    archidekt_url: str | None = None


class DeckSyncOut(BaseModel):
    deck_id: int
    name: str
    added: list[str] = []
    removed: list[str] = []
    changed: list[str] = []
    other: list[str] = []
    error: str | None = None
    summary: str


class DeckDetailOut(DeckOut):
    # Basic lands per land, e.g. {"Plains": 10, "Forest": 3}; counted by name, not tracked as cards.
    basic_lands: dict[str, int] = {}
    # Copies that are part of the deck but not tracked, e.g. {"basic": 13, "token": 5}.
    untracked: dict[str, int] = {}
    cards: list[DeckCardOut]
