"""Server-rendered pages (Jinja2) and the HTML forms behind them."""

from pathlib import PurePath
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from app import jobs
from app.cardmarket import is_cardmarket_url
from app.config import BASE_DIR
from app.csv_import import CsvFormatError, parse_collection_csv
from app.database import get_session
from app.deps import get_scryfall
from app.models import Card, CollectionEntry, Deck, WishlistEntry
from app.routers.api import load_deck
from app.scryfall import ScryfallClient, ScryfallError
from app.services import collection, decks, stats, wishlist

router = APIRouter(include_in_schema=False)
templates = Jinja2Templates(directory=BASE_DIR / "templates")


def format_eur(value: float | None) -> str:
    return "–" if value is None else f"€{value:,.2f}"


templates.env.filters["eur"] = format_eur


def render(request: Request, template: str, **context):
    context.setdefault("msg", request.query_params.get("msg"))
    context.setdefault("error", request.query_params.get("error") == "1")
    return templates.TemplateResponse(request, template, context)


def redirect(path: str, msg: str | None = None, error: bool = False) -> RedirectResponse:
    if msg:
        path += f"?msg={quote(msg)}" + ("&error=1" if error else "")
    return RedirectResponse(path, status_code=303)


def parse_money(text: str) -> float | None:
    """Accepts both '12.50' and '12,50'."""
    try:
        value = float(text.strip().replace("€", "").replace(",", "."))
    except ValueError:
        return None
    return value if value >= 0 else None


# --- dashboard & card detail -------------------------------------------------


@router.get("/")
def dashboard_page(request: Request, session: Session = Depends(get_session)):
    return render(request, "dashboard.html", **stats.dashboard(session))


@router.get("/cards/{card_id}")
def card_page(card_id: int, request: Request, session: Session = Depends(get_session)):
    card = session.get(Card, card_id)
    if card is None:
        raise HTTPException(status_code=404, detail="Card not found")
    owned = [row for row in collection.collection_rows(session) if row.card.id == card_id]
    wanted = [row for row in wishlist.wishlist_rows(session) if row.card.id == card_id]
    return render(
        request,
        "card.html",
        card=card,
        owned=owned,
        wanted=wanted,
        history=stats.price_history(session, card_id),
    )


# --- collection (HAVE) -------------------------------------------------------


@router.get("/collection")
def collection_page(request: Request, session: Session = Depends(get_session)):
    rows = collection.collection_rows(session)
    return render(request, "collection.html", rows=rows, total=collection.collection_value(rows))


@router.post("/collection/add")
async def collection_add(
    name: str = Form(""),
    set_code: str = Form(""),
    collector_number: str = Form(""),
    quantity: int = Form(1),
    is_foil: bool = Form(False),
    session: Session = Depends(get_session),
    scryfall: ScryfallClient = Depends(get_scryfall),
):
    try:
        entry = await collection.add_card_by_name(
            session, scryfall, name.strip(), set_code.strip() or None, max(1, quantity), is_foil,
            collector_number.strip().lstrip("#") or None,
        )
    except ScryfallError as exc:
        return redirect("/collection", str(exc), error=True)
    card = session.get(Card, entry.card_id)
    stats.record_daily_total(session)
    session.commit()
    return redirect("/collection", f"Added {card.name} ({card.set_code.upper()} #{card.collector_number})")


@router.post("/collection/import")
async def collection_import(
    file: UploadFile = File(...),
    replace: bool = Form(False),
    session: Session = Depends(get_session),
    scryfall: ScryfallClient = Depends(get_scryfall),
):
    try:
        parsed = parse_collection_csv(await file.read())
        result = await collection.import_rows(session, scryfall, parsed.rows, replace)
    except (CsvFormatError, ScryfallError) as exc:
        return redirect("/collection", str(exc), error=True)
    stats.record_daily_total(session)
    session.commit()
    msg = f"Imported {result.imported_cards} cards ({result.imported_rows} rows, {parsed.format} format)."
    problems = [f"not found: {name}" for name in result.not_found] + parsed.errors
    if problems:
        shown = "; ".join(problems[:10]) + ("; …" if len(problems) > 10 else "")
        msg += f" {len(problems)} skipped — {shown}"
    return redirect("/collection", msg, error=bool(problems))


@router.post("/collection/{entry_id}/quantity")
def collection_quantity(entry_id: int, quantity: int = Form(...), session: Session = Depends(get_session)):
    """Fallback for browsers without JavaScript; the page normally saves through the API."""
    entry = session.get(CollectionEntry, entry_id)
    if entry is None or quantity < 1:
        return redirect("/collection", "Enter a quantity of at least 1", error=True)
    collection.set_quantity(session, entry, quantity)
    stats.record_daily_total(session)
    session.commit()
    return redirect("/collection", "Quantity updated")


@router.post("/collection/{entry_id}/delete")
def collection_delete(entry_id: int, session: Session = Depends(get_session)):
    entry = session.get(CollectionEntry, entry_id)
    if entry is not None:
        session.delete(entry)
        stats.record_daily_total(session)
        session.commit()
    return redirect("/collection", "Removed from collection")


# --- wishlist (WANT) ---------------------------------------------------------


@router.get("/wishlist")
def wishlist_page(request: Request, session: Session = Depends(get_session)):
    rows = wishlist.wishlist_rows(session)
    return render(
        request,
        "wishlist.html",
        rows=rows,
        total=wishlist.wishlist_cost(rows),
        collection_value=collection.collection_value(collection.collection_rows(session)),
        alerts=wishlist.recent_alerts(session, 25),
    )


@router.post("/wishlist/add")
async def wishlist_add(
    name: str = Form(...),
    target_price: str = Form(...),
    set_code: str = Form(""),
    cardmarket_url: str = Form(""),
    session: Session = Depends(get_session),
    scryfall: ScryfallClient = Depends(get_scryfall),
):
    target = parse_money(target_price)
    url = cardmarket_url.strip() or None
    if target is None:
        return redirect("/wishlist", "Enter a valid maximum price", error=True)
    if url and not is_cardmarket_url(url):
        return redirect("/wishlist", "The Cardmarket URL must start with https://www.cardmarket.com", error=True)
    try:
        entry = await wishlist.add_wish_by_name(
            session, scryfall, name.strip(), target, set_code.strip() or None, url
        )
    except ScryfallError as exc:
        return redirect("/wishlist", str(exc), error=True)
    card = session.get(Card, entry.card_id)
    stats.record_daily_total(session)
    session.commit()
    return redirect("/wishlist", f"Watching {card.name} for {format_eur(target)} or less")


def _active_entry(session: Session, entry_id: int) -> WishlistEntry | None:
    entry = session.get(WishlistEntry, entry_id)
    return entry if entry is not None and entry.active else None


@router.post("/wishlist/clear")
def wishlist_clear(session: Session = Depends(get_session)):
    """The "Remove all" button."""
    removed = wishlist.clear_wishlist(session)
    stats.record_daily_total(session)
    session.commit()
    if not removed:
        return redirect("/wishlist", "Your wishlist was already empty")
    return redirect("/wishlist", f"Removed all {removed} card{'s' if removed != 1 else ''} from your wishlist")


@router.post("/wishlist/targets")
async def wishlist_targets(request: Request, session: Session = Depends(get_session)):
    """The "Save all prices" button: every target price on the page in one go."""
    targets: dict[int, float] = {}
    for key, value in (await request.form()).items():
        if not key.startswith("target:"):
            continue
        price = parse_money(str(value))
        if price is None or not key.removeprefix("target:").isdigit():
            return redirect("/wishlist", f"Nothing was saved: '{value}' is not a valid price", error=True)
        targets[int(key.removeprefix("target:"))] = price
    try:
        changed = wishlist.set_targets(session, targets)
    except ValueError as exc:
        session.rollback()
        return redirect("/wishlist", f"Nothing was saved: {exc}", error=True)
    session.commit()
    if not changed:
        return redirect("/wishlist", "No target prices were changed")
    return redirect("/wishlist", f"Saved {changed} target price{'s' if changed != 1 else ''}")


@router.post("/wishlist/{entry_id}/printing")
async def wishlist_printing(
    entry_id: int,
    scryfall_id: str = Form(""),
    session: Session = Depends(get_session),
    scryfall: ScryfallClient = Depends(get_scryfall),
):
    entry = _active_entry(session, entry_id)
    if entry is None or not scryfall_id.strip():
        return redirect("/wishlist", "Choose a set first", error=True)
    try:
        await wishlist.change_printing(session, scryfall, entry, scryfall_id.strip())
    except (ScryfallError, ValueError) as exc:
        session.rollback()
        return redirect("/wishlist", str(exc), error=True)
    card = session.get(Card, entry.card_id)
    stats.record_daily_total(session)
    session.commit()
    return redirect("/wishlist", f"Now watching {card.name} from {card.set_name} ({card.set_code.upper()})")


@router.post("/wishlist/{entry_id}/target")
def wishlist_target(entry_id: int, target_price: str = Form(...), session: Session = Depends(get_session)):
    entry, target = _active_entry(session, entry_id), parse_money(target_price)
    if entry is None or target is None:
        return redirect("/wishlist", "Enter a valid maximum price", error=True)
    entry.target_price = target
    session.add(entry)
    session.commit()
    return redirect("/wishlist", "Target price updated")


@router.post("/wishlist/{entry_id}/purchase")
def wishlist_purchase(entry_id: int, is_foil: bool = Form(False), session: Session = Depends(get_session)):
    entry = _active_entry(session, entry_id)
    if entry is None:
        return redirect("/wishlist", "Wishlist entry not found", error=True)
    card = session.get(Card, entry.card_id)
    wishlist.purchase(session, entry, 1, is_foil)
    stats.record_daily_total(session)
    session.commit()
    return redirect("/collection", f"{card.name} moved from wishlist to collection")


@router.post("/wishlist/{entry_id}/delete")
def wishlist_delete(entry_id: int, session: Session = Depends(get_session)):
    entry = _active_entry(session, entry_id)
    if entry is not None:
        entry.active = False
        session.add(entry)
        stats.record_daily_total(session)
        session.commit()
    return redirect("/wishlist", "Removed from wishlist")


# --- deck view ---------------------------------------------------------------


def _decklist_text(rows: list[decks.DeckRow]) -> str:
    return "\n".join(f"{row.needed} {row.name}" for row in rows)


@router.get("/deck")
def deck_page(request: Request, session: Session = Depends(get_session)):
    summaries = decks.deck_summaries(session)
    return render(request, "deck.html", rows=None, deck=None, summaries=summaries, brackets=decks.BRACKETS,
                  deck_data=_deck_data(summaries))


def _deck_data(summaries: list[decks.DeckSummary]) -> list[dict]:
    """What the deck randomizer on the page needs to know about every saved deck."""
    return [
        {
            "id": s.deck.id,
            "name": s.deck.name,
            "bracket": s.deck.bracket,
            "bracket_name": s.bracket_name,
            "complete": s.complete,
            "usable": s.usable,
            "needed": s.needed,
        }
        for s in summaries
    ]


@router.post("/deck")
async def deck_check(
    request: Request,
    decklist: str = Form(""),
    archidekt_url: str = Form(""),
    deck_name: str = Form(""),
    action: str = Form("check"),
    include_basics: bool = Form(False),
    include_tokens: bool = Form(False),
    file: UploadFile | None = File(None),
    session: Session = Depends(get_session),
):
    """Check a deck against the collection; with action=save it is stored as well."""
    uploaded = await file.read() if file is not None and file.filename else None
    summaries = decks.deck_summaries(session)
    try:
        loaded = await load_deck(decklist, archidekt_url, uploaded, include_basics, include_tokens)
    except decks.DeckError as exc:
        return render(request, "deck.html", rows=None, deck=None, summaries=summaries, decklist=decklist,
                      archidekt_url=archidekt_url, brackets=decks.BRACKETS, deck_data=_deck_data(summaries),
                      msg=str(exc), error=True)
    name, cards = loaded.name, loaded.cards
    if name is None and uploaded:
        name = PurePath(file.filename).stem
    name = deck_name.strip() or name or ""
    if action == "save":
        deck = decks.save_deck(session, name, cards, loaded.left_out, loaded.bracket)
        session.commit()
        return redirect(f"/decks/{deck.id}", f"Saved {deck.name}. The cards you own are now set aside for this deck.")
    rows = decks.check_deck(session, cards)
    return render(request, "deck.html", rows=rows, deck=None, summaries=summaries, deck_name=name,
                  decklist=_decklist_text(rows), archidekt_url=archidekt_url, left_out=loaded.left_out,
                  bracket=loaded.bracket, brackets=decks.BRACKETS, deck_data=_deck_data(summaries),
                  include_basics=include_basics, include_tokens=include_tokens)


@router.post("/deck/save")
async def deck_save(
    request: Request,
    name: str = Form(...),
    decklist: str = Form(...),
    skipped_token: int = Form(0),
    bracket: str = Form(""),
    session: Session = Depends(get_session),
):
    """Save the deck that was just checked; its list was already filtered by the check."""
    cards = decks.parse_decklist(decklist)
    if not cards:
        return redirect("/deck", "No cards found in that deck", error=True)
    try:
        left_out = decks.LeftOut(_basic_lands_from(await request.form()), max(0, skipped_token))
    except decks.DeckError as exc:
        return redirect("/deck", str(exc), error=True)
    deck = decks.save_deck(session, name, cards, left_out, int(bracket) if bracket.isdigit() else None)
    session.commit()
    return redirect(f"/decks/{deck.id}", f"Saved {deck.name}. The cards you own are now set aside for this deck.")


@router.get("/decks/{deck_id}")
def saved_deck_page(deck_id: int, request: Request, session: Session = Depends(get_session)):
    deck = session.get(Deck, deck_id)
    if deck is None:
        raise HTTPException(status_code=404, detail="Deck not found")
    return render(request, "deck.html", rows=decks.saved_deck_rows(session, deck), deck=deck,
                  summaries=decks.deck_summaries(session), left_out=decks.deck_left_out(session, deck),
                  bracket=deck.bracket, brackets=decks.BRACKETS, basic_names=decks.BASIC_LAND_NAMES,
                  tracked_basics=decks.tracked_basic_lands(session, deck))


def _basic_lands_from(form) -> dict[str, int]:
    """Read the "basic:<land>" fields of a form."""
    return decks.parse_basic_lands(
        {key.removeprefix("basic:"): value for key, value in form.items() if key.startswith("basic:")}
    )


@router.post("/decks/{deck_id}/save")
async def saved_deck_save(deck_id: int, request: Request, session: Session = Depends(get_session)):
    """The "Save changes" button: bracket, basic lands and chosen printings together."""
    deck = session.get(Deck, deck_id)
    if deck is None:
        return redirect("/deck", "Deck not found", error=True)
    form = await request.form()
    try:
        bracket = str(form.get("bracket", "")).strip()
        printings = {
            int(key.removeprefix("pick:")): int(value) if str(value).strip() else None
            for key, value in form.items()
            if key.startswith("pick:")
        }
        decks.update_deck(
            session,
            deck,
            int(bracket) if bracket else None,
            {key.removeprefix("basic:"): value for key, value in form.items() if key.startswith("basic:")},
            printings,
        )
        # The "Remove" / "Put back" buttons submit the same form, so pending edits are saved along.
        message = "Changes saved"
        for field, assigned in (("unassign", False), ("assign", True)):
            if str(form.get(field, "")).strip():
                entry = decks.set_assigned(session, deck, int(form[field]), assigned)
                message = (
                    f"{entry.name} is back in this deck" if assigned else f"{entry.name} moved to Still missing"
                )
    except (ValueError, decks.DeckError) as exc:
        session.rollback()
        message = str(exc) if isinstance(exc, decks.DeckError) else "That is not a valid value"
        return redirect(f"/decks/{deck_id}", f"Nothing was saved: {message}", error=True)
    session.commit()
    return redirect(f"/decks/{deck_id}", message)


@router.post("/decks/{deck_id}/basics")
async def saved_deck_basics(deck_id: int, request: Request, session: Session = Depends(get_session)):
    deck = session.get(Deck, deck_id)
    if deck is None:
        return redirect("/deck", "Deck not found", error=True)
    try:
        decks.set_basic_lands(session, deck, _basic_lands_from(await request.form()))
    except decks.DeckError as exc:
        return redirect(f"/decks/{deck_id}", str(exc), error=True)
    session.commit()
    return redirect(f"/decks/{deck_id}", "Basic lands updated")


@router.post("/decks/{deck_id}/basics/move")
def saved_deck_move_basics(deck_id: int, session: Session = Depends(get_session)):
    deck = session.get(Deck, deck_id)
    if deck is None:
        return redirect("/deck", "Deck not found", error=True)
    moved = decks.move_basic_lands_out(session, deck)
    session.commit()
    return redirect(f"/decks/{deck_id}", f"Moved {moved} basic lands out of the card list")


@router.post("/decks/{deck_id}/bracket")
def saved_deck_bracket(deck_id: int, bracket: str = Form(""), session: Session = Depends(get_session)):
    deck = session.get(Deck, deck_id)
    if deck is None:
        return redirect("/deck", "Deck not found", error=True)
    try:
        decks.set_bracket(session, deck, int(bracket) if bracket.strip() else None)
    except (ValueError, decks.DeckError):
        return redirect(f"/decks/{deck_id}", "A bracket is a number from 1 to 5", error=True)
    session.commit()
    return redirect(f"/decks/{deck_id}")


@router.post("/decks/{deck_id}/cards/{entry_id}/printing")
def saved_deck_printing(
    deck_id: int, entry_id: int, collection_entry_id: str = Form(""), session: Session = Depends(get_session)
):
    deck = session.get(Deck, deck_id)
    if deck is None:
        return redirect("/deck", "Deck not found", error=True)
    try:
        chosen = int(collection_entry_id) if collection_entry_id.strip() else None
        decks.choose_printing(session, deck, entry_id, chosen)
    except (ValueError, decks.DeckError) as exc:
        return redirect(f"/decks/{deck_id}", str(exc), error=True)
    session.commit()
    return redirect(f"/decks/{deck_id}")


@router.post("/decks/{deck_id}/delete")
def saved_deck_delete(deck_id: int, session: Session = Depends(get_session)):
    deck = session.get(Deck, deck_id)
    if deck is None:
        return redirect("/deck", "Deck not found", error=True)
    name = deck.name
    decks.delete_deck(session, deck)
    session.commit()
    return redirect("/deck", f"Deleted {name}. Its cards are available for other decks again.")


@router.post("/deck/wishlist")
async def deck_to_wishlist(
    names: list[str] = Form(default=[]),
    target_pct: float = Form(90.0),
    session: Session = Depends(get_session),
    scryfall: ScryfallClient = Depends(get_scryfall),
):
    try:
        added, not_found = await decks.add_missing_to_wishlist(session, scryfall, names, target_pct)
    except ScryfallError as exc:
        return redirect("/wishlist", str(exc), error=True)
    stats.record_daily_total(session)
    session.commit()
    msg = f"Added {added} missing cards to your wishlist."
    if not_found:
        msg += " Not found: " + ", ".join(not_found[:10])
    return redirect("/wishlist", msg, error=bool(not_found))


# --- manual job triggers -----------------------------------------------------


@router.post("/jobs/update-prices")
def job_update_prices(background: BackgroundTasks):
    background.add_task(jobs.run_collection_update)
    return redirect("/", "Price update started — refresh in a moment")


@router.post("/jobs/check-wishlist")
def job_check_wishlist(background: BackgroundTasks):
    background.add_task(jobs.run_wishlist_check)
    return redirect("/wishlist", "Wishlist check started — refresh in a moment")
