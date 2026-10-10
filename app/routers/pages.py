"""Server-rendered pages (Jinja2) and the HTML forms behind them."""

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
from app.models import Card, CollectionEntry, WishlistEntry
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
    name: str = Form(...),
    set_code: str = Form(""),
    quantity: int = Form(1),
    is_foil: bool = Form(False),
    session: Session = Depends(get_session),
    scryfall: ScryfallClient = Depends(get_scryfall),
):
    try:
        entry = await collection.add_card_by_name(
            session, scryfall, name.strip(), set_code.strip() or None, max(1, quantity), is_foil
        )
    except ScryfallError as exc:
        return redirect("/collection", str(exc), error=True)
    card = session.get(Card, entry.card_id)
    stats.record_daily_total(session)
    session.commit()
    return redirect("/collection", f"Added {card.name} ({card.set_code.upper()})")


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


@router.get("/deck")
def deck_page(request: Request):
    return render(request, "deck.html", rows=None)


@router.post("/deck")
async def deck_check(
    request: Request,
    decklist: str = Form(""),
    archidekt_url: str = Form(""),
    session: Session = Depends(get_session),
):
    try:
        cards = await load_deck(decklist, archidekt_url)
    except decks.DeckError as exc:
        return render(request, "deck.html", rows=None, decklist=decklist, archidekt_url=archidekt_url,
                      msg=str(exc), error=True)
    rows = decks.check_deck(session, cards)
    return render(request, "deck.html", rows=rows, decklist=decklist, archidekt_url=archidekt_url)


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
