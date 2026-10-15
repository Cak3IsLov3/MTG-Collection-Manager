"""JSON API."""

from dataclasses import dataclass
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from sqlmodel import Session, select

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
    CollectionUpdate,
    DeckCardOut,
    DeckCheckIn,
    DeckDetailOut,
    DeckLinkIn,
    DeckAssignedIn,
    DeckBracketIn,
    DeckCardAdd,
    DeckOut,
    DeckPrintingIn,
    DeckSaveIn,
    DeckSyncOut,
    DeckCommanderIn,
    DeckPlaysOut,
    GameIn,
    GameOut,
    DeckTokensIn,
    DeckNeedOut,
    DeckUseOut,
    ImportOut,
    PrintingOut,
    PurchaseIn,
    SummaryOut,
    WishlistAdd,
    WishlistItemOut,
    WishlistOut,
    WishlistPrintingIn,
)
from app.scryfall import CardNotFound, InvalidCardRequest, ScryfallClient, ScryfallError
from app.services import collection, decks, games, stats, wishlist

router = APIRouter(prefix="/api", tags=["api"])


def _scryfall_http_error(exc: ScryfallError) -> HTTPException:
    if isinstance(exc, CardNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, InvalidCardRequest):
        return HTTPException(status_code=400, detail=str(exc))
    return HTTPException(status_code=502, detail=str(exc))


@router.get("/summary/weekly")
def weekly(session: Session = Depends(get_session)):
    """The week in short, as sent to Discord: collection value and change, risers and fallers, games."""
    summary = stats.weekly_summary(session)
    for key in ("risers", "fallers"):
        summary[key] = [
            {"name": m.card.name, "card_id": m.card.id, "foil": m.is_foil, "old_price": m.old_price, "new_price": m.new_price}
            for m in summary[key]
        ]
    summary["lines"] = stats.weekly_lines(stats.weekly_summary(session))
    return summary


@router.get("/summary", response_model=SummaryOut)
def summary(session: Session = Depends(get_session)):
    return stats.dashboard(session)


@router.get("/collection", response_model=CollectionOut)
def list_collection(
    q: str | None = None,
    in_deck: bool | None = None,
    colors: str | None = None,
    color_match: Literal["any", "within", "exact"] = "any",
    spare: bool = False,
    session: Session = Depends(get_session),
):
    """The collection; `q` filters on card name, set name or set code (every word must match).
    `in_deck=false` keeps the entries with a copy that is in no saved deck, `in_deck=true` the
    entries a saved deck uses. `colors` filters on colour identity (letters from WUBRG, C for
    colourless): cards with any of them, cards that fit `within` them, or cards with `exact`ly them.
    `spare=true` keeps the entries with copies that no deck uses or needs (basic lands left out),
    for example to trade or sell."""
    rows = collection.search_rows(collection.collection_rows(session), q)
    rows = collection.filter_colors(rows, colors, color_match)
    shared = decks.collection_decks(session)
    usage, wanted = shared.usage, shared.wanted
    items = []
    kept = []
    for row in rows:
        item = CollectionItemOut.from_row(row)
        item.in_decks = [
            DeckUseOut(id=deck.id, name=deck.name, quantity=copies) for deck, copies in usage.get(row.entry.id, [])
        ]
        item.free = row.entry.quantity - sum(use.quantity for use in item.in_decks)
        item.wanted_by = [
            DeckNeedOut(id=deck.id, name=deck.name, quantity=copies, entry_id=deck_entry_id)
            for deck, copies, deck_entry_id in wanted.get(row.entry.id, [])
        ]
        item.waiting = shared.waiting.get(row.entry.id, 0)
        basic = row.card.name.casefold() in decks.BASIC_LANDS
        item.spare = 0 if item.wanted_by or basic else item.free
        if spare and item.spare <= 0:
            continue
        if in_deck is None or (bool(item.in_decks) if in_deck else item.free > 0):
            items.append(item)
            kept.append(row)
    return CollectionOut(total_value=collection.collection_value(kept), items=items)


@router.post("/collection", response_model=CollectionItemOut, status_code=201)
async def add_to_collection(
    payload: CollectionAdd,
    session: Session = Depends(get_session),
    scryfall: ScryfallClient = Depends(get_scryfall),
):
    try:
        entry = await collection.add_card_by_name(
            session, scryfall, (payload.name or "").strip(), payload.set_code, payload.quantity, payload.is_foil,
            (payload.collector_number or "").strip() or None,
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
    """Importing adds to the collection; `?replace=true` replaces what earlier imports brought in
    with the file's contents. Cards added by hand stay."""
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
        kept_manual=result.kept_manual,
        new_copies=result.new_copies,
    )


@router.patch("/collection/{entry_id}", response_model=CollectionItemOut)
async def update_collection_entry(
    entry_id: int,
    payload: CollectionUpdate,
    session: Session = Depends(get_session),
    scryfall: ScryfallClient = Depends(get_scryfall),
):
    """Change the number of copies of a card in the collection, or whether they are foil.

    Changing the finish can merge the entry into an existing one for the same
    printing; the response is the entry that holds the copies afterwards.
    """
    if payload.quantity is None and payload.is_foil is None:
        raise HTTPException(status_code=400, detail="Send a quantity, is_foil, or both")
    entry = session.get(CollectionEntry, entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Collection entry not found")
    if payload.quantity is not None:
        collection.set_quantity(session, entry, payload.quantity)
    if payload.is_foil is not None:
        entry = await collection.change_finish(session, scryfall, entry, payload.is_foil)
    stats.record_daily_total(session)
    session.commit()
    return CollectionItemOut.from_row(collection.collection_rows(session, entry.id)[0])


@router.delete("/collection/{entry_id}", status_code=204)
def remove_from_collection(entry_id: int, session: Session = Depends(get_session)):
    entry = session.get(CollectionEntry, entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Collection entry not found")
    session.delete(entry)
    stats.record_daily_total(session)
    session.commit()


@router.get("/wishlist", response_model=WishlistOut)
def list_wishlist(
    colors: str | None = None,
    color_match: Literal["any", "within", "exact"] = "any",
    session: Session = Depends(get_session),
):
    """The wishlist; `colors` (letters from WUBRG, C for colourless) keeps the cards with any of
    them, the cards that fit `within` them, or the cards with `exact`ly them."""
    rows = wishlist.wishlist_rows(session)
    if colors:
        rows = [row for row in rows if collection.color_match(row.card.colors, colors, color_match)]
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


@router.delete("/wishlist")
def clear_wishlist(session: Session = Depends(get_session)):
    """Stop watching every card on the wishlist. The alert history is kept."""
    removed = wishlist.clear_wishlist(session)
    stats.record_daily_total(session)
    session.commit()
    return {"removed": removed}


@router.get("/tokens")
async def list_token_versions(name: str, scryfall: ScryfallClient = Depends(get_scryfall)):
    """The different tokens with a name ("Angel": 4/4 flying, 1/1 lifelink, ...), one print of each."""
    from app.services.deck_insights import token_versions

    try:
        versions = await token_versions(scryfall, name)
    except ScryfallError as exc:
        raise _scryfall_http_error(exc) from exc
    return [version.__dict__ for version in versions]


@router.get("/printings", response_model=list[PrintingOut])
async def list_printings(name: str, scryfall: ScryfallClient = Depends(get_scryfall)):
    """Every paper printing of a card with its price, newest first; for choosing a set."""
    try:
        found = await scryfall.printings(name.strip())
    except ScryfallError as exc:
        raise _scryfall_http_error(exc) from exc
    return [
        PrintingOut(
            scryfall_id=card.scryfall_id, set_code=card.set_code, set_name=card.set_name,
            collector_number=card.collector_number, image_url=card.image_url,
            price_eur=card.price_eur, price_eur_foil=card.price_eur_foil,
        )
        for card in found
    ]


@router.put("/wishlist/{entry_id}/printing", response_model=WishlistItemOut)
async def change_wishlist_printing(
    entry_id: int,
    payload: WishlistPrintingIn,
    session: Session = Depends(get_session),
    scryfall: ScryfallClient = Depends(get_scryfall),
):
    """Watch another printing (set) of the same card. The target price stays as it is."""
    entry = _wishlist_entry(session, entry_id)
    try:
        await wishlist.change_printing(session, scryfall, entry, payload.scryfall_id)
    except ScryfallError as exc:
        session.rollback()
        raise _scryfall_http_error(exc) from exc
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    stats.record_daily_total(session)
    session.commit()
    return WishlistItemOut.from_row(wishlist.wishlist_rows(session, entry_id)[0])


@router.put("/wishlist/targets", response_model=WishlistOut)
def set_wishlist_targets(targets: dict[int, float], session: Session = Depends(get_session)):
    """Set several target prices at once: {wishlist entry id: price}. All of them or, on an error, none."""
    try:
        wishlist.set_targets(session, targets)
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    session.commit()
    rows = wishlist.wishlist_rows(session)
    return WishlistOut(total_cost=wishlist.wishlist_cost(rows), items=[WishlistItemOut.from_row(row) for row in rows])


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
        loaded = await load_deck(
            payload.decklist, payload.archidekt_url, None, payload.include_basic_lands, payload.include_tokens
        )
    except decks.DeckError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return [DeckCardOut.from_row(row) for row in decks.check_deck(session, loaded.cards)]


def _deck_out(summary: decks.DeckSummary) -> DeckOut:
    return DeckOut(
        id=summary.deck.id, name=summary.deck.name, cards_needed=summary.needed,
        cards_usable=summary.usable, complete=summary.complete, value=summary.value,
        bracket=summary.deck.bracket, bracket_name=summary.bracket_name, basic_land_count=summary.basic_lands,
        deck_size=summary.total_cards, deck_owned=summary.total_owned,
        archidekt_url=decks.deck_url(summary.deck), synced_at=summary.deck.synced_at, colors=summary.deck.colors,
        uses_tokens=summary.uses_tokens, token_count=summary.tokens,
        commander=summary.deck.commander or None, commander_image=summary.deck.commander_image or None,
    )


def _deck_detail(session: Session, deck: Deck) -> DeckDetailOut:
    summary = next(s for s in decks.deck_summaries(session) if s.deck.id == deck.id)
    cards = [DeckCardOut.from_row(row) for row in decks.saved_deck_rows(session, deck)]
    left_out = decks.deck_left_out(session, deck)
    untracked = {kind: n for kind, n in (("basic", left_out.basic_total), ("token", left_out.tokens)) if n}
    return DeckDetailOut(
        **_deck_out(summary).model_dump(), cards=cards, basic_lands=left_out.basics, untracked=untracked
    )


@router.get("/decks", response_model=list[DeckOut])
async def list_decks(
    bracket: int | None = None,
    colors: str | None = None,
    color_match: Literal["any", "within", "exact"] = "any",
    q: str | None = None,
    session: Session = Depends(get_session),
    scryfall: ScryfallClient = Depends(get_scryfall),
):
    """The saved decks; `bracket` (1-5) keeps only the decks in that Commander bracket, `colors`
    (letters from WUBRG, C for colourless) the decks with any, `within` or `exact`ly those colours,
    and `q` the decks whose name or one of whose cards contains every word."""
    try:
        await decks.fill_deck_colors(session, scryfall)
        await decks.fill_deck_commanders(session, scryfall)
    except ScryfallError:
        session.rollback()  # the list still works, new decks just have no colours or picture yet
    summaries = decks.deck_summaries(session)
    if q:
        summaries = decks.search_decks(summaries, decks.deck_card_names(session), q)
    return [
        _deck_out(summary)
        for summary in summaries
        if (bracket is None or summary.deck.bracket == bracket)
        and (not colors or collection.color_match(summary.deck.colors, colors, color_match))
    ]


@router.post("/decks", response_model=DeckDetailOut, status_code=201)
async def save_deck(payload: DeckSaveIn, session: Session = Depends(get_session)):
    """Save a deck; the cards you own are set aside for it and no longer count for newer decks."""
    try:
        loaded = await load_deck(
            payload.decklist, payload.archidekt_url, None, payload.include_basic_lands, payload.include_tokens
        )
    except decks.DeckError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    deck = decks.save_deck(
        session, payload.name, loaded.cards, loaded.left_out, payload.bracket or loaded.bracket, loaded.archidekt_id
    )
    session.commit()
    return _deck_detail(session, deck)


@router.get("/decks/random", response_model=list[DeckOut])
def random_decks(
    count: int = 1,
    bracket: int | None = None,
    complete_only: bool = False,
    session: Session = Depends(get_session),
):
    """Pick `count` different saved decks at random, optionally only from one bracket or only complete ones."""
    try:
        picked = decks.random_decks(decks.deck_summaries(session), count, bracket, complete_only)
    except decks.DeckError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return [_deck_out(summary) for summary in picked]


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


@router.post("/decks/{deck_id}/cards", response_model=DeckDetailOut, status_code=201)
async def add_deck_card(
    deck_id: int,
    payload: DeckCardAdd,
    session: Session = Depends(get_session),
    scryfall: ScryfallClient = Depends(get_scryfall),
):
    """Add a card to a saved deck: by name, or by set + collector number for one exact printing."""
    deck = _saved_deck(session, deck_id)
    try:
        await decks.add_card(
            session, scryfall, deck, (payload.name or "").strip(), (payload.set_code or "").strip() or None,
            (payload.collector_number or "").strip() or None, payload.quantity,
        )
    except ScryfallError as exc:
        session.rollback()
        raise _scryfall_http_error(exc) from exc
    except decks.DeckError as exc:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    session.commit()
    return _deck_detail(session, deck)


@router.put("/decks/{deck_id}/cards/{entry_id}/assigned", response_model=DeckDetailOut)
def set_assigned(deck_id: int, entry_id: int, payload: DeckAssignedIn, session: Session = Depends(get_session)):
    """Take your copies of a card out of a deck, so it counts as missing, or put them back."""
    deck = _saved_deck(session, deck_id)
    try:
        decks.set_assigned(session, deck, entry_id, payload.assigned)
    except decks.DeckError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    session.commit()
    return _deck_detail(session, deck)


@router.post("/decks/{deck_id}/cards/{entry_id}/new-copy", response_model=DeckDetailOut)
def put_new_copy_in_deck(
    deck_id: int, entry_id: int, payload: DeckPrintingIn | None = None, session: Session = Depends(get_session)
):
    """Put a new copy from an import, that waits to be sorted, in this deck. `collection_entry_id`
    picks the printing; without it the printing the deck list names goes first, then the cheapest."""
    deck = _saved_deck(session, deck_id)
    try:
        decks.put_new_copy_in_deck(session, deck, entry_id, payload.collection_entry_id if payload else None)
    except decks.DeckError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    session.commit()
    return _deck_detail(session, deck)


@router.post("/collection/let-app-sort")
def let_app_sort(entry_id: int | None = None, session: Session = Depends(get_session)):
    """Stop holding new copies from imports back (all, or of one collection entry): the app
    shares them out over the decks again."""
    released = decks.let_app_sort(session, entry_id)
    session.commit()
    return {"released": released}


@router.get("/games", response_model=list[GameOut])
def list_games(deck_id: int | None = None, session: Session = Depends(get_session)):
    """The game log, newest first; `deck_id` keeps the games of one deck."""
    return games.recent_games(session, deck_id=deck_id)


@router.post("/games", response_model=GameOut, status_code=201)
def log_game(payload: GameIn, session: Session = Depends(get_session)):
    """Log a game played with one of your decks."""
    deck = _saved_deck(session, payload.deck_id)
    try:
        game = games.log_game(session, deck, payload.result, payload.played_on, payload.players, payload.notes)
    except games.GameError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    session.commit()
    return game


@router.delete("/games/{game_id}", status_code=204)
def delete_game(game_id: int, session: Session = Depends(get_session)):
    try:
        games.delete_game(session, game_id)
    except games.GameError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    session.commit()


@router.get("/games/stats", response_model=list[DeckPlaysOut])
def game_stats(session: Session = Depends(get_session)):
    """Per deck that was played: games, wins, losses, draws, win rate and the last time."""
    plays = games.deck_plays(session)
    return [
        DeckPlaysOut(
            deck_id=deck.id, deck_name=deck.name, played=p.played, wins=p.wins, losses=p.losses, draws=p.draws,
            win_rate=p.win_rate, last_played=p.last_played,
        )
        for deck in session.exec(select(Deck).order_by(Deck.id))
        if (p := plays.get(deck.id))
    ]


@router.get("/decks/{deck_id}/value-history")
def deck_value_history(deck_id: int, session: Session = Depends(get_session)):
    """What the copies this deck uses now were worth on each day with prices: {"labels": [...], "values": [...]}."""
    return decks.deck_value_history(session, _saved_deck(session, deck_id))


@router.put("/decks/{deck_id}/commander", response_model=DeckDetailOut)
async def set_commander(
    deck_id: int, payload: DeckCommanderIn, session: Session = Depends(get_session),
    scryfall: ScryfallClient = Depends(get_scryfall),
):
    """Choose the commander of a deck (a card in it); null for none. Its picture is fetched from Scryfall."""
    deck = _saved_deck(session, deck_id)
    try:
        decks.set_commander(session, deck, payload.commander)
    except decks.DeckError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    session.commit()
    try:
        await decks.fill_deck_commanders(session, scryfall)
    except ScryfallError:
        session.rollback()
    return _deck_detail(session, deck)


@router.put("/decks/{deck_id}/tokens/{token_id}/quantity", response_model=DeckDetailOut)
def set_token_quantity(deck_id: int, token_id: int, payload: dict[str, int], session: Session = Depends(get_session)):
    """Change how many of a token a deck needs: {"quantity": 3}."""
    deck = _saved_deck(session, deck_id)
    try:
        decks.set_token_quantity(session, deck, token_id, int(payload.get("quantity", 0)))
    except decks.DeckError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    session.commit()
    return _deck_detail(session, deck)


@router.put("/decks/{deck_id}/tokens", response_model=DeckDetailOut)
def set_uses_tokens(deck_id: int, payload: DeckTokensIn, session: Session = Depends(get_session)):
    """Mark a deck as one that uses tokens, or not; null goes back to what its deck list says."""
    deck = _saved_deck(session, deck_id)
    decks.set_uses_tokens(session, deck, payload.uses_tokens)
    session.commit()
    return _deck_detail(session, deck)


@router.put("/decks/{deck_id}/basic-lands", response_model=DeckDetailOut)
def set_basic_lands(deck_id: int, counts: dict[str, int], session: Session = Depends(get_session)):
    """Set how many of each basic land a deck runs, e.g. {"Plains": 10, "Forest": 3}. Replaces the old numbers."""
    deck = _saved_deck(session, deck_id)
    try:
        decks.set_basic_lands(session, deck, counts)
    except decks.DeckError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    session.commit()
    return _deck_detail(session, deck)


@router.post("/decks/{deck_id}/basic-lands/move", response_model=DeckDetailOut)
def move_basic_lands(deck_id: int, session: Session = Depends(get_session)):
    """Move basic lands that are still in the card list of a deck to its per-land counts."""
    deck = _saved_deck(session, deck_id)
    decks.move_basic_lands_out(session, deck)
    session.commit()
    return _deck_detail(session, deck)


def _sync_out(result: decks.SyncResult) -> DeckSyncOut:
    return DeckSyncOut(
        deck_id=result.deck.id, name=result.deck.name, added=result.added, removed=result.removed,
        changed=result.changed, other=result.other, error=result.error, summary=result.summary(),
    )


@router.post("/decks/sync", response_model=list[DeckSyncOut])
async def sync_all_decks(session: Session = Depends(get_session)):
    """Bring every deck that follows an Archidekt link up to date."""
    results = await decks.sync_all_decks(session)
    session.commit()
    return [_sync_out(result) for result in results]


@router.post("/decks/{deck_id}/sync", response_model=DeckSyncOut)
async def sync_deck(deck_id: int, session: Session = Depends(get_session)):
    """Bring one deck up to date with the Archidekt deck it follows."""
    deck = _saved_deck(session, deck_id)
    try:
        result = await decks.sync_deck(session, deck)
    except decks.DeckError as exc:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    session.commit()
    return _sync_out(result)


@router.put("/decks/{deck_id}/archidekt", response_model=DeckDetailOut)
async def set_archidekt_link(deck_id: int, payload: DeckLinkIn, session: Session = Depends(get_session)):
    """Let a deck follow an Archidekt deck (it is brought up to date right away), or stop following with null."""
    deck = _saved_deck(session, deck_id)
    try:
        if decks.set_archidekt_link(session, deck, payload.archidekt_url) and deck.archidekt_id:
            await decks.sync_deck(session, deck)
    except decks.DeckError as exc:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    session.commit()
    return _deck_detail(session, deck)


@router.put("/decks/{deck_id}/bracket", response_model=DeckDetailOut)
def set_bracket(deck_id: int, payload: DeckBracketIn, session: Session = Depends(get_session)):
    """Set the Commander bracket (1-5) of a deck, or null to clear it."""
    deck = _saved_deck(session, deck_id)
    decks.set_bracket(session, deck, payload.bracket)
    session.commit()
    return _deck_detail(session, deck)


def _saved_deck(session: Session, deck_id: int) -> Deck:
    deck = session.get(Deck, deck_id)
    if deck is None:
        raise HTTPException(status_code=404, detail="Deck not found")
    return deck


@router.delete("/decks/{deck_id}", status_code=204)
def delete_deck(deck_id: int, session: Session = Depends(get_session)):
    deck = session.get(Deck, deck_id)
    if deck is None:
        raise HTTPException(status_code=404, detail="Deck not found")
    decks.delete_deck(session, deck)
    session.commit()


@dataclass
class LoadedDeck:
    name: str | None  # only when the source has one
    cards: list[decks.DeckCard]  # the cards to track against the collection
    left_out: decks.LeftOut  # basic lands and tokens
    bracket: int | None = None
    archidekt_id: int | None = None  # set when the deck came from an Archidekt link


async def load_deck(
    decklist: str | None,
    archidekt_url: str | None,
    file_content: bytes | None = None,
    include_basics: bool = False,
    include_tokens: bool = False,
) -> LoadedDeck:
    name = bracket = deck_id = None
    if archidekt_url and archidekt_url.strip():
        deck_id = decks.archidekt_deck_id(archidekt_url)
        if deck_id is None:
            raise decks.DeckError("That does not look like an Archidekt deck URL")
        data = await decks.fetch_archidekt_json(deck_id)
        name, cards = decks.parse_archidekt_deck(data)
        bracket = decks.archidekt_bracket(data)
    elif file_content:
        cards = decks.parse_deck_file(file_content)
    else:
        cards = decks.parse_decklist(decklist or "")
    if not cards:
        raise decks.DeckError("No cards found in that deck")
    cards, left_out = decks.select_tracked(cards, include_basics, include_tokens)
    if not cards:
        raise decks.DeckError("That deck only has basic lands and tokens; tick the boxes to include them")
    return LoadedDeck(name, cards, left_out, bracket, deck_id)


@router.post("/jobs/update-prices", status_code=202)
def trigger_price_update(background: BackgroundTasks):
    background.add_task(jobs.run_collection_update)
    return {"status": "started"}


@router.post("/jobs/check-wishlist", status_code=202)
def trigger_wishlist_check(background: BackgroundTasks):
    background.add_task(jobs.run_wishlist_check)
    return {"status": "started"}
