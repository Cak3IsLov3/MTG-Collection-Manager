"""Server-rendered pages (Jinja2) and the HTML forms behind them."""

import datetime as dt
import json
import logging
from pathlib import PurePath
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import PlainTextResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from app import backup, jobs
from app.cardmarket import is_cardmarket_url
from app.config import BASE_DIR, get_settings
from app.csv_import import CsvFormatError, parse_collection_csv
from app.database import get_session
from app.deps import get_edhrec, get_scryfall, get_spellbook
from app.edhrec import EdhrecClient, EdhrecError
from app.models import Card, CollectionEntry, Deck, WishlistEntry
from app.routers.api import load_deck
from app.scryfall import CardRef, ScryfallClient, ScryfallError, name_key
from app.services import collection, combos, deck_insights, decks, edhrec_compare, games, reprints, stats, suggestions, wishlist
from app.spellbook import SpellbookClient, SpellbookError
from app.services.exports import collection_csv, wishlist_csv

logger = logging.getLogger(__name__)
router = APIRouter(include_in_schema=False)
templates = Jinja2Templates(directory=BASE_DIR / "templates")


def format_eur(value: float | None) -> str:
    return "–" if value is None else f"€{value:,.2f}"


templates.env.filters["eur"] = format_eur


def asset_version() -> int:
    """Changes whenever the stylesheet does, so browsers never keep showing an old cached copy."""
    return int((BASE_DIR / "static" / "style.css").stat().st_mtime)


templates.env.globals["asset_version"] = asset_version


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
    return render(request, "dashboard.html", settings=get_settings(), backups=backup.list_backups(),
                  reprint_alerts=reprints.recent_reprint_alerts(session), today=dt.date.today(),
                  backup_possible=backup.database_path(get_settings().database_url) is not None,
                  **stats.dashboard(session))


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
    shared = decks.collection_decks(session)
    return render(request, "collection.html", rows=rows, total=collection.collection_value(rows),
                  usage=shared.usage, wanted=shared.wanted, waiting=shared.waiting,
                  basic_lands=decks.BASIC_LAND_NAMES, purchases=collection.purchase_summary(rows))


@router.post("/collection/add")
async def collection_add(
    name: str = Form(""),
    set_code: str = Form(""),
    collector_number: str = Form(""),
    quantity: int = Form(1),
    is_foil: bool = Form(False),
    purchase_price: str = Form(""),
    session: Session = Depends(get_session),
    scryfall: ScryfallClient = Depends(get_scryfall),
):
    paid = parse_money(purchase_price) if purchase_price.strip() else None
    if purchase_price.strip() and paid is None:
        return redirect("/collection", "Fill in what you paid as a price, like 2.50", error=True)
    try:
        entry = await collection.add_card_by_name(
            session, scryfall, name.strip(), set_code.strip() or None, max(1, quantity), is_foil,
            collector_number.strip().lstrip("#") or None, paid,
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
    if result.new_copies:
        msg += f" {result.new_copies} new cop{'y' if result.new_copies == 1 else 'ies'} wait for you to put them in decks."
    if result.kept_manual:
        msg += f" Kept {result.kept_manual} cards you added yourself."
    problems = [f"not found: {name}" for name in result.not_found] + parsed.errors
    if problems:
        shown = "; ".join(problems[:10]) + ("; …" if len(problems) > 10 else "")
        msg += f" {len(problems)} skipped — {shown}"
    return redirect("/collection", msg, error=bool(problems))


@router.post("/collection/{entry_id}/sort")
def collection_sort(
    entry_id: int, deck_id: int = Form(...), deck_entry_id: int = Form(...), session: Session = Depends(get_session)
):
    """Put a new copy from an import in a deck that misses it."""
    deck = session.get(Deck, deck_id)
    if deck is None:
        return redirect("/collection", "That deck does not exist anymore", error=True)
    try:
        message = decks.put_new_copy_in_deck(session, deck, deck_entry_id, entry_id)
    except decks.DeckError as exc:
        return redirect("/collection", str(exc), error=True)
    session.commit()
    return redirect("/collection", message)


@router.post("/collection/{entry_id}/let-app-sort")
def collection_let_app_sort(entry_id: int, session: Session = Depends(get_session)):
    """Stop holding the new copies of one card back: the app shares them out over the decks."""
    released = decks.let_app_sort(session, entry_id)
    session.commit()
    return redirect("/collection", f"The app now shares out {released} new cop{'y' if released == 1 else 'ies'} over your decks")


@router.post("/collection/let-app-sort")
def collection_let_app_sort_all(session: Session = Depends(get_session)):
    """Stop holding any new copies back."""
    released = decks.let_app_sort(session)
    session.commit()
    return redirect("/collection", f"The app now shares out {released} new cop{'y' if released == 1 else 'ies'} over your decks")


@router.get("/collection/export.csv")
def collection_export(session: Session = Depends(get_session)):
    """The collection as a CSV in Moxfield's format: Moxfield, Archidekt and this app can import it."""
    return Response(collection_csv(session), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="collection.csv"'})


@router.get("/wishlist/export.csv")
def wishlist_export(session: Session = Depends(get_session)):
    return Response(wishlist_csv(session), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="wishlist.csv"'})


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


@router.post("/collection/{entry_id}/paid")
def collection_paid(entry_id: int, purchase_price: str = Form(""), session: Session = Depends(get_session)):
    """Fallback for browsers without JavaScript; the page normally saves through the API."""
    entry = session.get(CollectionEntry, entry_id)
    paid = parse_money(purchase_price) if purchase_price.strip() else None
    if entry is None or (purchase_price.strip() and paid is None):
        return redirect("/collection", "Fill in what you paid as a price, like 2.50", error=True)
    collection.set_purchase_price(session, entry, paid)
    session.commit()
    return redirect("/collection", "Purchase price saved")


@router.post("/collection/{entry_id}/delete")
def collection_delete(entry_id: int, session: Session = Depends(get_session)):
    entry = session.get(CollectionEntry, entry_id)
    if entry is not None:
        session.delete(entry)
        stats.record_daily_total(session)
        session.commit()
    return redirect("/collection", "Removed from collection")


# --- wishlist (WANT) ---------------------------------------------------------


# --- game log ---------------------------------------------------------------


@router.get("/games")
def games_page(request: Request, deck: int | None = None, session: Session = Depends(get_session)):
    saved = list(session.exec(select(Deck)))
    plays = games.deck_plays(session)
    by_id = {d.id: d for d in saved}
    stats_rows = sorted(
        ((by_id[deck_id], p) for deck_id, p in plays.items() if deck_id in by_id),
        key=lambda item: (item[1].last_played, item[1].played),
        reverse=True,
    )
    return render(
        request, "games.html", decks=saved, games=games.recent_games(session), stats=stats_rows,
        not_played=sorted((d for d in saved if d.id not in plays), key=lambda d: d.name),
        deck_names={d.id: d.name for d in saved}, results=games.RESULTS,
        today=dt.date.today().isoformat(), selected_deck=deck,
        overview=games.log_stats(session), players=games.known_players(session),
        last_commanders=games.last_commanders(session), game_commanders=games.game_commanders,
        nights=games.game_nights(session, 30), webhook=bool(get_settings().discord_webhook_url),
        brackets=decks.BRACKETS,
    )


@router.get("/stream")
def stream_page():
    return RedirectResponse("/stream/control", status_code=307)


@router.post("/decks/{deck_id}/stream")
async def saved_deck_stream(deck_id: int, session: Session = Depends(get_session)):
    """Put the tokens of this deck in the stream overlay's control panel and open it."""
    from app.main import stream_app
    from app.services.stream_link import StreamLinkError, deck_for_stream
    from app.stream.server import set_deck

    deck = session.get(Deck, deck_id)
    if deck is None:
        return redirect("/deck", "Deck not found", error=True)
    try:
        await set_deck(stream_app, await deck_for_stream(session, deck))
    except StreamLinkError as exc:
        return redirect(f"/decks/{deck_id}", f"Could not get the tokens from Scryfall: {exc}", error=True)
    return RedirectResponse("/stream/control#add", status_code=303)


@router.get("/table")
def table_page(request: Request, deck: int | None = None, session: Session = Depends(get_session)):
    """The live table: life, poison, commander damage and turns on one screen; logged afterwards.
    `deck` chooses the deck to play in the setup."""
    return render(request, "table.html", decks=list(session.exec(select(Deck))), players=games.known_players(session),
                  last_commanders=games.last_commanders(session), chosen_deck=deck)


@router.post("/games")
def games_log(
    deck_id: int = Form(...),
    result: str = Form(...),
    played_on: str = Form(""),
    players: str = Form(""),
    notes: str = Form(""),
    opponents: str = Form(""),
    winner: str = Form(""),
    opponent_commanders: str = Form(""),
    back: str = Form(""),
    session: Session = Depends(get_session),
):
    """Log a game; `back` sends you back to the deck page you logged it from."""
    target = back if back.startswith("/decks/") else "/games"
    deck = session.get(Deck, deck_id)
    if deck is None:
        return redirect(target, "That deck does not exist anymore", error=True)
    try:
        day = dt.date.fromisoformat(played_on) if played_on.strip() else None
        commanders = json.loads(opponent_commanders) if opponent_commanders.strip() else {}
        game = games.log_game(
            session, deck, result, day, int(players) if players.strip() else None, notes, opponents, winner,
            commanders if isinstance(commanders, dict) else {},
        )
    except ValueError as exc:
        message = str(exc) if isinstance(exc, games.GameError) else "That is not a valid date or number"
        return redirect(target, message, error=True)
    session.commit()
    return redirect(target, f"Logged: {games.RESULTS[game.result].lower()} with {deck.name} on {game.played_on}")


@router.post("/games/nights/{night}/discord")
async def game_night_discord(night: str, session: Session = Depends(get_session)):
    from app.deps import get_notifier

    try:
        day = dt.date.fromisoformat(night)
    except ValueError:
        return redirect("/games", "That is not a date", error=True)
    if await jobs.send_game_night(session, get_notifier(), day):
        return redirect("/games", f"The recap of {day} went to Discord")
    return redirect("/games", "Not sent: no games that day, no Discord webhook, or Discord refused it", error=True)


@router.post("/games/{game_id}/delete")
def games_delete(game_id: int, session: Session = Depends(get_session)):
    try:
        games.delete_game(session, game_id)
    except games.GameError as exc:
        return redirect("/games", str(exc), error=True)
    session.commit()
    return redirect("/games", "Game removed from the log")


@router.get("/wishlist")
def wishlist_page(request: Request, session: Session = Depends(get_session)):
    rows = wishlist.wishlist_rows(session)
    settings = get_settings()
    drops = {}
    if settings.price_drop_pct > 0:
        drops = {
            row.entry.id: drop
            for row, drop in stats.find_price_drops(
                session, settings.price_drop_days, settings.price_drop_pct, settings.price_drop_min_eur, rows
            )
        }
    return render(
        request,
        "wishlist.html",
        rows=rows,
        drops=drops,
        settings=settings,
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
def wishlist_purchase(
    entry_id: int, is_foil: bool = Form(False), paid: str = Form(""), session: Session = Depends(get_session)
):
    entry = _active_entry(session, entry_id)
    if entry is None:
        return redirect("/wishlist", "Wishlist entry not found", error=True)
    price = parse_money(paid) if paid.strip() else None
    if paid.strip() and price is None:
        return redirect("/wishlist", "Fill in what you paid as a price, like 2.50", error=True)
    card = session.get(Card, entry.card_id)
    wishlist.purchase(session, entry, 1, is_foil, price)
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
    """The checked deck as text for the "save" form; keeps the printing a card asks for."""
    lines = []
    for row in rows:
        line = f"{row.needed} {row.name}"
        if row.set_code:
            line += f" ({row.set_code})" + (f" {row.collector_number}" if row.collector_number else "")
        lines.append(line + (" *F*" if row.foil else ""))
    return "\n".join(lines)


def _wishlist_refs(cards: list[str]) -> list[CardRef]:
    """Hidden "name|set|number|scryfall id" fields of the deck page, as card references."""
    refs = []
    for value in cards:
        name, set_code, number, scryfall_id = (value.split("|") + ["", "", ""])[:4]
        if name.strip():
            refs.append(CardRef(name.strip(), set_code or None, number or None, scryfall_id or None))
    return refs


@router.get("/deck")
async def deck_page(
    request: Request, session: Session = Depends(get_session), scryfall: ScryfallClient = Depends(get_scryfall)
):
    await _fill_deck_colors(session, scryfall)
    summaries = decks.deck_summaries(session)
    plays = games.deck_plays(session)
    warnings = deck_insights.bracket_warnings(session, [s.deck for s in summaries], await _game_changers(scryfall))
    return render(request, "deck.html", rows=None, deck=None, summaries=summaries, brackets=decks.BRACKETS,
                  deck_data=_deck_data(summaries, plays), card_names=decks.deck_card_names(session), plays=plays,
                  bracket_warnings=warnings)


async def _game_changers(scryfall: ScryfallClient) -> frozenset[str] | None:
    """The Game Changers list; None when Scryfall cannot be reached (the check then says so)."""
    try:
        return await deck_insights.game_changers(scryfall)
    except ScryfallError as exc:
        logger.warning("Could not fetch the Game Changers list: %s", exc)
        return None


async def _fill_deck_colors(session: Session, scryfall: ScryfallClient) -> None:
    """New or changed decks get their colours and commander picture; without Scryfall the list
    still shows, just without them."""
    try:
        await decks.fill_deck_colors(session, scryfall)
        await decks.fill_deck_commanders(session, scryfall)
    except ScryfallError as exc:
        logger.warning("Could not work out deck colours or commanders: %s", exc)
        session.rollback()


def _deck_data(summaries: list[decks.DeckSummary], plays: dict | None = None) -> list[dict]:
    """What the deck randomizer on the page needs to know about every saved deck."""
    plays = plays or {}
    return [
        {
            "id": s.deck.id,
            "name": s.deck.name,
            "bracket": s.deck.bracket,
            "bracket_name": s.bracket_name,
            "complete": s.complete,
            "usable": s.usable,
            "needed": s.needed,
            "commander_image": s.deck.commander_image or None,
            "played": plays[s.deck.id].played if s.deck.id in plays else 0,
            "days_since_played": plays[s.deck.id].days_since() if s.deck.id in plays else None,
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
                      card_names=decks.deck_card_names(session),
                      msg=str(exc), error=True)
    name, cards = loaded.name, loaded.cards
    if name is None and uploaded:
        name = PurePath(file.filename).stem
    name = deck_name.strip() or name or ""
    if action == "save":
        deck = decks.save_deck(session, name, cards, loaded.left_out, loaded.bracket, loaded.archidekt_id)
        session.commit()
        return redirect(f"/decks/{deck.id}", f"Saved {deck.name}. The cards you own are now set aside for this deck.")
    rows = decks.check_deck(session, cards)
    return render(request, "deck.html", rows=rows, deck=None, summaries=summaries, deck_name=name,
                  decklist=_decklist_text(rows), archidekt_url=archidekt_url, left_out=loaded.left_out,
                  bracket=loaded.bracket, brackets=decks.BRACKETS, deck_data=_deck_data(summaries),
                  card_names=decks.deck_card_names(session),
                  archidekt_id=loaded.archidekt_id,
                  include_basics=include_basics, include_tokens=include_tokens)


@router.post("/deck/save")
async def deck_save(
    request: Request,
    name: str = Form(...),
    decklist: str = Form(...),
    skipped_token: int = Form(0),
    bracket: str = Form(""),
    archidekt_id: str = Form(""),
    session: Session = Depends(get_session),
):
    """Save the deck that was just checked; its list was already filtered by the check."""
    cards = decks.parse_decklist(decklist)
    if not cards:
        return redirect("/deck", "No cards found in that deck", error=True)
    try:
        form = await request.form()
        token_cards = [
            decks.DeckCard(key.removeprefix("token:"), int(value), kind=decks.TOKEN)
            for key, value in form.items()
            if key.startswith("token:") and str(value).isdigit() and int(value) > 0
        ]
        left_out = decks.LeftOut(_basic_lands_from(form), max(0, skipped_token), token_cards)
    except decks.DeckError as exc:
        return redirect("/deck", str(exc), error=True)
    deck = decks.save_deck(
        session, name, cards, left_out, int(bracket) if bracket.isdigit() else None,
        int(archidekt_id) if archidekt_id.isdigit() else None,
    )
    session.commit()
    return redirect(f"/decks/{deck.id}", f"Saved {deck.name}. The cards you own are now set aside for this deck.")


@router.get("/decks/{deck_id}")
async def saved_deck_page(
    deck_id: int, request: Request, session: Session = Depends(get_session),
    scryfall: ScryfallClient = Depends(get_scryfall), spellbook: SpellbookClient = Depends(get_spellbook),
    edhrec: EdhrecClient = Depends(get_edhrec),
):
    deck = session.get(Deck, deck_id)
    if deck is None:
        raise HTTPException(status_code=404, detail="Deck not found")
    await _fill_deck_colors(session, scryfall)  # also the commander picture, e.g. after choosing one
    try:
        await deck_insights.fill_card_info(session, scryfall, deck.id)  # types, mana values, pictures
    except ScryfallError as exc:
        logger.warning("Could not look up the cards of %s: %s", deck.name, exc)
        session.rollback()
    try:
        deck_combos = await combos.check_deck_combos(session, deck, spellbook)
        session.commit()
    except SpellbookError as exc:
        logger.warning("Could not check the combos of %s: %s", deck.name, exc)
        deck_combos = combos.deck_combos(deck) if combos.is_current(session, deck) else combos.DeckCombos()
    try:
        deck_suggestions = await suggestions.suggest(session, scryfall, deck)
    except ScryfallError as exc:
        logger.warning("Could not look up cards for the suggestions: %s", exc)
        session.rollback()
        deck_suggestions = await suggestions.suggest(session, None, deck)
    edhrec_error = None
    try:
        edhrec_comparison = await edhrec_compare.compare_deck(session, deck, edhrec, scryfall)
    except EdhrecError as exc:
        logger.warning("EDHREC: %s", exc)
        edhrec_comparison, edhrec_error = None, str(exc)
    summaries = decks.deck_summaries(session)
    summary = next(s for s in summaries if s.deck.id == deck.id)
    return render(request, "deck.html", rows=decks.saved_deck_rows(session, deck), deck=deck, uses_tokens=summary.uses_tokens,
                  value_history=decks.deck_value_history(session, deck),
                  deck_token_list=decks.deck_tokens(session, deck), deck_stats=deck_insights.deck_stats(session, deck),
                  token_suggestions=await _token_suggestions(session, scryfall, deck),
                  standard_tokens=get_settings().standard_tokens,
                  land_images=await _land_images(scryfall),
                  bracket_check=deck_insights.bracket_check(session, deck, await _game_changers(scryfall), deck_combos),
                  deck_combos=deck_combos, owned=combos.owned_missing(session, deck_combos), deck_suggestions=deck_suggestions,
                  edhrec=edhrec_comparison, edhrec_error=edhrec_error,
                  weakest=edhrec_compare.weakest_cards(session, deck, edhrec_comparison) if edhrec_comparison else None,
                  edhrec_floor=edhrec_compare.lowest_listed(edhrec_comparison) if edhrec_comparison else 0,
                  deck_plays=games.deck_plays(session).get(deck.id), deck_games=games.recent_games(session, 5, deck.id),
                  results=games.RESULTS, today=dt.date.today().isoformat(),
                  summaries=summaries, left_out=decks.deck_left_out(session, deck),
                  bracket=deck.bracket, brackets=decks.BRACKETS, basic_names=decks.BASIC_LAND_NAMES,
                  tracked_basics=decks.tracked_basic_lands(session, deck), deck_link=decks.deck_url(deck))


def _basic_lands_from(form) -> dict[str, int]:
    """Read the "basic:<land>" fields of a form."""
    return decks.parse_basic_lands(
        {key.removeprefix("basic:"): value for key, value in form.items() if key.startswith("basic:")}
    )


@router.post("/decks/{deck_id}/cards")
async def saved_deck_add_card(
    deck_id: int,
    name: str = Form(""),
    set_code: str = Form(""),
    collector_number: str = Form(""),
    quantity: int = Form(1),
    session: Session = Depends(get_session),
    scryfall: ScryfallClient = Depends(get_scryfall),
):
    """The "Add a card" form on a deck page; works like the one in the collection."""
    deck = session.get(Deck, deck_id)
    if deck is None:
        return redirect("/deck", "Deck not found", error=True)
    try:
        message = await decks.add_card(
            session, scryfall, deck, name.strip(), set_code.strip() or None,
            collector_number.strip().lstrip("#") or None, max(1, quantity),
        )
    except (ScryfallError, decks.DeckError) as exc:
        session.rollback()
        return redirect(f"/decks/{deck_id}", str(exc), error=True)
    session.commit()
    return redirect(f"/decks/{deck_id}", message)


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
        if "commander" in form and str(form["commander"]) != (deck.commander or ""):
            decks.set_commander(session, deck, str(form["commander"]))
        if "tokens_shown" in form:  # an unticked box is not sent at all
            decks.set_uses_tokens(session, deck, "uses_tokens" in form)
        # The "Remove" / "Put back" buttons submit the same form, so pending edits are saved along.
        message = "Changes saved"
        # A new Archidekt link: follow it from now on and take over that deck right away.
        if "archidekt_url" in form and decks.set_archidekt_link(session, deck, str(form["archidekt_url"])):
            message = (
                (await decks.sync_deck(session, deck)).summary()
                if deck.archidekt_id
                else "Changes saved. This deck no longer follows an Archidekt deck."
            )
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


@router.post("/decks/{deck_id}/sync")
async def saved_deck_sync(deck_id: int, session: Session = Depends(get_session)):
    """The "Sync now" button on a deck that follows an Archidekt link."""
    deck = session.get(Deck, deck_id)
    if deck is None:
        return redirect("/deck", "Deck not found", error=True)
    try:
        result = await decks.sync_deck(session, deck)
    except decks.DeckError as exc:
        session.rollback()
        return redirect(f"/decks/{deck_id}", f"Could not sync: {exc}", error=True)
    session.commit()
    return redirect(f"/decks/{deck_id}", result.summary())


@router.post("/decks/{deck_id}/cards/{entry_id}/new-copy")
def saved_deck_new_copy(deck_id: int, entry_id: int, session: Session = Depends(get_session)):
    """Put a new copy from an import, that waits to be sorted, in this deck."""
    deck = session.get(Deck, deck_id)
    if deck is None:
        return redirect("/deck", "Deck not found", error=True)
    try:
        message = decks.put_new_copy_in_deck(session, deck, entry_id)
    except decks.DeckError as exc:
        return redirect(f"/decks/{deck_id}", str(exc), error=True)
    session.commit()
    return redirect(f"/decks/{deck_id}", message)


@router.post("/decks/{deck_id}/tokens")
def saved_deck_add_token(
    deck_id: int, name: str = Form(""), quantity: int = Form(1), session: Session = Depends(get_session)
):
    deck = session.get(Deck, deck_id)
    if deck is None:
        return redirect("/deck", "Deck not found", error=True)
    try:
        token = decks.add_token(session, deck, name, quantity)
    except decks.DeckError as exc:
        return redirect(f"/decks/{deck_id}", str(exc), error=True)
    session.commit()
    message = f"Added {quantity}× {token.name} to the tokens of this deck."
    need = next((s for s in deck_insights.token_suggestions(session, deck) if name_key(s.name) == name_key(token.name)), None)
    if need is not None:
        message += f" The cards of this deck make about {need.needed}; you have {need.have.quantity if need.have else 0} in the list."
    return redirect(f"/decks/{deck_id}", message)


@router.post("/decks/{deck_id}/tokens/suggested")
def saved_deck_suggested_tokens(deck_id: int, scryfall_id: str = Form(""), session: Session = Depends(get_session)):
    """Take over the suggested tokens: one (`scryfall_id`) or all of them."""
    deck = session.get(Deck, deck_id)
    if deck is None:
        return redirect("/deck", "Deck not found", error=True)
    changed = deck_insights.apply_token_suggestions(session, deck, scryfall_id.strip() or None)
    session.commit()
    if not changed:
        return redirect(f"/decks/{deck_id}", "The token list already has these tokens")
    return redirect(f"/decks/{deck_id}", f"Token list updated: {changed} token{'s' if changed != 1 else ''}")


@router.post("/decks/{deck_id}/tokens/{token_id}/quantity")
def saved_deck_token_quantity(
    deck_id: int, token_id: int, quantity: int = Form(...), session: Session = Depends(get_session)
):
    deck = session.get(Deck, deck_id)
    if deck is None:
        return redirect("/deck", "Deck not found", error=True)
    try:
        token = decks.set_token_quantity(session, deck, token_id, quantity)
    except decks.DeckError as exc:
        return redirect(f"/decks/{deck_id}", str(exc), error=True)
    session.commit()
    return redirect(f"/decks/{deck_id}", f"{token.name}: {token.quantity} now")


@router.post("/decks/{deck_id}/tokens/{token_id}/delete")
def saved_deck_remove_token(deck_id: int, token_id: int, session: Session = Depends(get_session)):
    deck = session.get(Deck, deck_id)
    if deck is None:
        return redirect("/deck", "Deck not found", error=True)
    try:
        decks.remove_token(session, deck, token_id)
    except decks.DeckError as exc:
        return redirect(f"/decks/{deck_id}", str(exc), error=True)
    session.commit()
    return redirect(f"/decks/{deck_id}", "Token removed")


@router.post("/decks/{deck_id}/tokens/{token_id}/version")
async def saved_deck_token_version(
    deck_id: int, token_id: int, scryfall_id: str = Form(...), session: Session = Depends(get_session),
    scryfall: ScryfallClient = Depends(get_scryfall),
):
    """Choose which of the tokens with that name the deck uses (there are many Angels)."""
    deck = session.get(Deck, deck_id)
    if deck is None:
        return redirect("/deck", "Deck not found", error=True)
    try:
        token = await deck_insights.choose_token_version(session, scryfall, deck, token_id, scryfall_id)
    except (ValueError, ScryfallError) as exc:
        return redirect(f"/decks/{deck_id}", f"Could not change the token: {exc}", error=True)
    session.commit()
    return redirect(f"/decks/{deck_id}", f"Changed the {token.name} token")


async def _token_suggestions(session: Session, scryfall: ScryfallClient, deck: Deck) -> list:
    found = deck_insights.token_suggestions(session, deck)
    try:
        await deck_insights.fill_token_images(scryfall, found)
    except ScryfallError as exc:
        logger.warning("Could not fetch the pictures of the suggested tokens: %s", exc)
    return found


async def _land_images(scryfall: ScryfallClient) -> dict[str, str]:
    """Pictures of the basic lands; without Scryfall the lands are shown by name."""
    try:
        return await deck_insights.basic_land_images(scryfall)
    except ScryfallError as exc:
        logger.warning("Could not fetch the basic land pictures: %s", exc)
        return {}


def _download_name(name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in " -_" else "_" for ch in name).strip() or "deck"


@router.get("/decks/{deck_id}/export.txt")
def saved_deck_export(deck_id: int, session: Session = Depends(get_session)):
    """The deck as a text list for Archidekt or Moxfield."""
    deck = session.get(Deck, deck_id)
    if deck is None:
        raise HTTPException(status_code=404, detail="Deck not found")
    text = deck_insights.export_text(session, deck, decks.saved_deck_rows(session, deck))
    filename = quote(f"{_download_name(deck.name)}.txt")
    return PlainTextResponse(text, headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"})


@router.get("/decks/{deck_id}/proxies")
async def saved_deck_proxies(
    deck_id: int, request: Request, all: bool = False, session: Session = Depends(get_session),
    scryfall: ScryfallClient = Depends(get_scryfall),
):
    """A printable sheet of the missing cards (or the whole deck), to play-test with."""
    deck = session.get(Deck, deck_id)
    if deck is None:
        raise HTTPException(status_code=404, detail="Deck not found")
    try:
        await deck_insights.fill_card_info(session, scryfall, deck.id)
    except ScryfallError:
        session.rollback()
    rows = decks.saved_deck_rows(session, deck)
    return render(request, "proxies.html", deck=deck, sheet=deck_insights.proxies(session, rows, everything=all),
                  everything=all)


@router.post("/deck/sync")
async def decks_sync_all(session: Session = Depends(get_session)):
    """The "Sync all with Archidekt" button on the decks page."""
    results = await decks.sync_all_decks(session)
    session.commit()
    if not results:
        return redirect("/deck", "None of your decks follows an Archidekt link yet")
    updated = [result for result in results if result.anything]
    failed = [result for result in results if result.error]
    message = f"Checked {len(results)} deck{'s' if len(results) != 1 else ''} on Archidekt: {len(updated)} updated"
    if updated:
        message += " (" + ", ".join(result.deck.name for result in updated[:5]) + ("…" if len(updated) > 5 else "") + ")"
    if failed:
        message += f", {len(failed)} could not be reached (" + ", ".join(result.deck.name for result in failed[:5]) + ")"
    return redirect("/deck", message, error=bool(failed))


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
    cards: list[str] = Form(default=[]),
    target_pct: float = Form(90.0),
    session: Session = Depends(get_session),
    scryfall: ScryfallClient = Depends(get_scryfall),
):
    try:
        added, not_found = await decks.add_missing_to_wishlist(
            session, scryfall, [*_wishlist_refs(cards), *names], target_pct
        )
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


@router.post("/jobs/backup")
def job_backup():
    try:
        made = backup.make_backup()
    except OSError as exc:
        return redirect("/", f"Backup failed: {exc}", error=True)
    if made is None:
        return redirect("/", "Nothing to back up: backups are off (BACKUP_KEEP=0) or the database is not a file", error=True)
    return redirect("/", f"Backup made: {made}")


@router.post("/jobs/check-wishlist")
def job_check_wishlist(background: BackgroundTasks):
    background.add_task(jobs.run_wishlist_check)
    return redirect("/wishlist", "Wishlist check started — refresh in a moment")
