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


class CollectionAdd(BaseModel):
    # Either a name (optionally with a set), or a set + collector number for one exact printing.
    name: str | None = None
    set_code: str | None = None
    collector_number: str | None = None
    quantity: int = Field(default=1, ge=1)
    is_foil: bool = False


class CollectionUpdate(BaseModel):
    quantity: int = Field(ge=1)


class CollectionItemOut(BaseModel):
    id: int
    card: CardOut
    quantity: int
    is_foil: bool
    added_at: dt.datetime
    unit_price: float | None
    total_price: float | None

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
            below_target=row.below_target,
        )


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


class DeckPickOut(BaseModel):
    quantity: int
    printing: CollectionItemOut


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
            using=[DeckPickOut(quantity=n, printing=CollectionItemOut.from_row(p)) for p, n in row.picks],
        )


class DeckSaveIn(DeckCheckIn):
    name: str = Field(min_length=1)


class DeckOut(BaseModel):
    id: int
    name: str
    cards_needed: int
    cards_usable: int
    complete: bool
    value: float


class DeckDetailOut(DeckOut):
    cards: list[DeckCardOut]
