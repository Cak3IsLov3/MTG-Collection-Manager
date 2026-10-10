"""JSON API."""

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from sqlmodel import Session

from app import jobs
from app.cardmarket import is_cardmarket_url
from app.csv_import import CsvFormatError, parse_collection_csv
from app.database import get_session
from app.deps import get_scryfall
from app.models import Card, CollectionEntry, Deck, WishlistEntry
from app.schemas import (
    AlertOut,
    CollectionAdd,
    CollectionItemOut,
    CollectionOut,
    DeckCardOut,
    DeckCheckIn,
    DeckDetailOut,
    DeckOut,
    DeckPrintingIn,
    DeckSaveIn,
    ImportOut,
    PurchaseIn,
    SummaryOut,
    WishlistAdd,
    WishlistItemOut,
    WishlistOut,
)
from app.scryfall import CardNotFound, ScryfallClient, ScryfallError
from app.services import collection, decks, stats, wishlist

router = APIRouter(prefix="/api", tags=["api"])


def _scryfall_http_error(exc: ScryfallError) -> HTTPException:
    if isinstance(exc, CardNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    return HTTPException(status_code=502, detail=str(exc))


@router.get("/summary", response_model=SummaryOut)
def summary(session: Session = Depends(get_session)):
    return stats.dashboard(session)


@router.get("/collection", response_model=CollectionOut)
def list_collection(session: Session = Depends(get_session)):
    rows = collection.collection_rows(session)
    return CollectionOut(
        total_value=collection.collection_value(rows),
        items=[CollectionItemOut.from_row(row) for row in rows],
    )


@router.post("/collection", response_model=CollectionItemOut, status_code=201)
async def add_to_collection(
    payload: CollectionAdd,
    session: Session = Depends(get_session),
    scryfall: ScryfallClient = Depends(get_scryfall),
):
    try:
        entry = await collection.add_card_by_name(
            session, scryfall, payload.name, payload.set_code, payload.quantity, payload.is_foil
        )
    except ScryfallError as exc:
        raise _scryfall_http_error(exc) from exc
    stats.record_daily_total(session)
    session.commit()
    return CollectionItemOut.from_row(collection.collection_rows(session, entry.id)[0])


@router.post("/collection/import", response_model=ImportOut)
async def import_collection(
    file: UploadFile = File(...),
    replace: bool = False,
    session: Session = Depends(get_session),
    scryfall: ScryfallClient = Depends(get_scryfall),
):
    """Importing adds to the collection; `?replace=true` replaces it with the file's contents."""
    try:
        parsed = parse_collection_csv(await file.read())
        result = await collection.import_rows(session, scryfall, parsed.rows, replace)
    except CsvFormatError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ScryfallError as exc:
        raise _scryfall_http_error(exc) from exc
    stats.record_daily_total(session)
    session.commit()
    return ImportOut(
        format=parsed.format,
        imported_rows=result.imported_rows,
        imported_cards=result.imported_cards,
        not_found=result.not_found,
        errors=parsed.errors,
    )


@router.delete("/collection/{entry_id}", status_code=204)
def remove_from_collection(entry_id: int, session: Session = Depends(get_session)):
    entry = session.get(CollectionEntry, entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Collection entry not found")
    session.delete(entry)
    stats.record_daily_total(session)
    session.commit()


@router.get("/wishlist", response_model=WishlistOut)
def list_wishlist(session: Session = Depends(get_session)):
    rows = wishlist.wishlist_rows(session)
    return WishlistOut(
        total_cost=wishlist.wishlist_cost(rows),
        items=[WishlistItemOut.from_row(row) for row in rows],
    )


@router.post("/wishlist", response_model=WishlistItemOut, status_code=201)
async def add_to_wishlist(
    payload: WishlistAdd,
    session: Session = Depends(get_session),
    scryfall: ScryfallClient = Depends(get_scryfall),
):
    if payload.cardmarket_url and not is_cardmarket_url(payload.cardmarket_url):
        raise HTTPException(status_code=400, detail="cardmarket_url must be an https://www.cardmarket.com URL")
    try:
        entry = await wishlist.add_wish_by_name(
            session, scryfall, payload.name, payload.target_price, payload.set_code, payload.cardmarket_url
        )
    except ScryfallError as exc:
        raise _scryfall_http_error(exc) from exc
    stats.record_daily_total(session)
    session.commit()
    return WishlistItemOut.from_row(wishlist.wishlist_rows(session, entry.id)[0])


def _wishlist_entry(session: Session, entry_id: int) -> WishlistEntry:
    entry = session.get(WishlistEntry, entry_id)
    if entry is None or not entry.active:
        raise HTTPException(status_code=404, detail="Wishlist entry not found")
    return entry


@router.post("/wishlist/{entry_id}/purchase", response_model=CollectionItemOut)
def purchase(entry_id: int, payload: PurchaseIn | None = None, session: Session = Depends(get_session)):
    """WANT -> HAVE: move a wishlist card into the collection."""
    payload = payload or PurchaseIn()
    bought = wishlist.purchase(session, _wishlist_entry(session, entry_id), payload.quantity, payload.is_foil)
    stats.record_daily_total(session)
    session.commit()
    return CollectionItemOut.from_row(collection.collection_rows(session, bought.id)[0])


@router.delete("/wishlist/{entry_id}", status_code=204)
def remove_from_wishlist(entry_id: int, session: Session = Depends(get_session)):
    entry = _wishlist_entry(session, entry_id)
    entry.active = False
    session.add(entry)
    stats.record_daily_total(session)
    session.commit()


@router.get("/alerts", response_model=list[AlertOut])
def list_alerts(limit: int = 50, session: Session = Depends(get_session)):
    return [
        AlertOut(
            id=alert.id,
            card_name=card.name,
            sent_at=alert.sent_at,
            price_at_alert=alert.price_at_alert,
            target_price=entry.target_price,
        )
        for alert, entry, card in wishlist.recent_alerts(session, limit)
    ]


@router.get("/cards/{card_id}/prices")
def card_prices(card_id: int, session: Session = Depends(get_session)):
    if session.get(Card, card_id) is None:
        raise HTTPException(status_code=404, detail="Card not found")
    return stats.price_history(session, card_id)


@router.post("/decks/check", response_model=list[DeckCardOut])
async def check_deck(payload: DeckCheckIn, session: Session = Depends(get_session)):
    """Have/missing overview for a deck, without saving it."""
    try:
        _, cards = await load_deck(payload.decklist, payload.archidekt_url)
    except decks.DeckError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return [DeckCardOut.from_row(row) for row in decks.check_deck(session, cards)]


def _deck_out(summary: decks.DeckSummary) -> DeckOut:
    return DeckOut(
        id=summary.deck.id, name=summary.deck.name, cards_needed=summary.needed,
        cards_usable=summary.usable, complete=summary.complete, value=summary.value,
    )


def _deck_detail(session: Session, deck: Deck) -> DeckDetailOut:
    summary = next(s for s in decks.deck_summaries(session) if s.deck.id == deck.id)
    cards = [DeckCardOut.from_row(row) for row in decks.saved_deck_rows(session, deck)]
    return DeckDetailOut(**_deck_out(summary).model_dump(), cards=cards)


@router.get("/decks", response_model=list[DeckOut])
def list_decks(session: Session = Depends(get_session)):
    return [_deck_out(summary) for summary in decks.deck_summaries(session)]


@router.post("/decks", response_model=DeckDetailOut, status_code=201)
async def save_deck(payload: DeckSaveIn, session: Session = Depends(get_session)):
    """Save a deck; the cards you own are set aside for it and no longer count for newer decks."""
    try:
        _, cards = await load_deck(payload.decklist, payload.archidekt_url)
    except decks.DeckError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    deck = decks.save_deck(session, payload.name, cards)
    session.commit()
    return _deck_detail(session, deck)


@router.get("/decks/{deck_id}", response_model=DeckDetailOut)
def get_deck(deck_id: int, session: Session = Depends(get_session)):
    deck = session.get(Deck, deck_id)
    if deck is None:
        raise HTTPException(status_code=404, detail="Deck not found")
    return _deck_detail(session, deck)


@router.put("/decks/{deck_id}/cards/{entry_id}/printing", response_model=DeckDetailOut)
def choose_printing(
    deck_id: int, entry_id: int, payload: DeckPrintingIn, session: Session = Depends(get_session)
):
    """Choose which printing from the collection a deck uses for a card (null = let the app choose)."""
    deck = session.get(Deck, deck_id)
    if deck is None:
        raise HTTPException(status_code=404, detail="Deck not found")
    try:
        decks.choose_printing(session, deck, entry_id, payload.collection_entry_id)
    except decks.DeckError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    session.commit()
    return _deck_detail(session, deck)


@router.delete("/decks/{deck_id}", status_code=204)
def delete_deck(deck_id: int, session: Session = Depends(get_session)):
    deck = session.get(Deck, deck_id)
    if deck is None:
        raise HTTPException(status_code=404, detail="Deck not found")
    decks.delete_deck(session, deck)
    session.commit()


async def load_deck(
    decklist: str | None, archidekt_url: str | None, file_content: bytes | None = None
) -> tuple[str | None, list[decks.DeckCard]]:
    """Returns (deck name if the source has one, cards)."""
    name = None
    if archidekt_url and archidekt_url.strip():
        deck_id = decks.archidekt_deck_id(archidekt_url)
        if deck_id is None:
            raise decks.DeckError("That does not look like an Archidekt deck URL")
        name, cards = await decks.fetch_archidekt_deck(deck_id)
    elif file_content:
        cards = decks.parse_deck_file(file_content)
    else:
        cards = decks.parse_decklist(decklist or "")
    if not cards:
        raise decks.DeckError("No cards found in that deck")
    return name, cards


@router.post("/jobs/update-prices", status_code=202)
def trigger_price_update(background: BackgroundTasks):
    background.add_task(jobs.run_collection_update)
    return {"status": "started"}


@router.post("/jobs/check-wishlist", status_code=202)
def trigger_wishlist_check(background: BackgroundTasks):
    background.add_task(jobs.run_wishlist_check)
    return {"status": "started"}
