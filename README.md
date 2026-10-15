# MTG Collection Manager

One app for your Magic: The Gathering finances: track what your collection is
worth over time **and** get a Discord alert when a card you want drops to your
price.

- **HAVE** – your collection, valued daily with Scryfall prices
- **WANT** – your wishlist, watched against Cardmarket prices

> "Your collection is worth €850, your wishlist still costs €120."

![Dashboard: collection value, wishlist cost and value over time](docs/dashboard.jpg)

| Top cards and weekly movers | Wishlist with targets, sorted by current price |
| --- | --- |
| ![Top 10 most valuable cards, risers and fallers](docs/dashboard-cards.jpg) | ![Wishlist sorted from expensive to cheap](docs/wishlist.jpg) |

| Collection: add by name or card number, edit quantities, tick foil | Saved decks with their commander, colours, games played and value |
| --- | --- |
| ![Collection](docs/collection.jpg) | ![Saved decks with their commander, colours, bracket, progress, value and how often they were played](docs/decks.jpg) |

![Wishlist targets set to 85% of the current price: the changed prices are highlighted until you save them all](docs/wishlist-targets.jpg)

![The wishlist filtered on colour: the red and green cards, each with its colours](docs/wishlist-colors.jpg)

![Choosing the set of a wishlist card: every printing of Force of Will with its price](docs/wishlist-set.jpg)

![Remove all asks for a second click before it empties the wishlist](docs/wishlist-remove-all.jpg)

![Importing a CSV with "Replace my imported cards": the cards added by hand are kept](docs/collection-import.jpg)

![After an import the new cards wait for you: no deck gets them until you put them in one](docs/collection-new-cards.jpg)

![Sorting the new cards: every deck that needs a card, with a "Put in this deck" button, or let the app decide](docs/collection-sort.jpg)

![On the deck page a missing card with a new copy waiting can be put in the deck directly](docs/deck-new-copy.jpg)

![Searching the collection: Sol Ring is one line with its five versions opened underneath, told apart by set and card number](docs/collection-search.jpg)

![Spare copies sorted by value: what no deck uses or needs, and what it is worth together](docs/collection-spare.jpg)

![Which decks a card is in: the label opened for two Sol Ring printings](docs/collection-decks.jpg)

![Filtering on colour: the cards that fit a blue-black deck, each with its colour identity](docs/collection-colors.jpg)

![Free copies a deck still needs: cards you own that are missing from a deck, with the decks that need them](docs/collection-needed.jpg)

![Printings without a price of their own borrow one from another printing, marked ≈ SET (Dragon Egg and Fellwar Stone)](docs/collection-borrowed.jpg)

![Searching the saved decks for a card: every deck that runs Sol Ring, with the matching card under its name](docs/decks-search.jpg)

![Only the decks with tokens: the decks marked "tokens", so you know which ones need tokens at the table](docs/decks-tokens.jpg)

![The saved decks filtered on bracket 3: two of the six decks are left](docs/decks-filter.jpg)

![The saved decks filtered on colour: the decks with green in them, each with its colours](docs/decks-colors.jpg)

![The saved decks sorted on missing cards: the decks closest to done first](docs/decks-sorted.jpg)

![A saved deck of exactly 100 cards: its commander, bracket, "Has tokens", deck size, games played and its value over time](docs/deck.jpg)

![Logging a game from the deck page, and the value of the deck's copies over the last month](docs/deck-value.jpg)

![The tokens and emblems of a deck in alphabetical order, with their pictures and a tick box per token for packing, and its mana curve and card types](docs/deck-tokens.jpg)

![Parts of a deck folded away by clicking their title: only what you need stays open](docs/deck-folded.jpg)

![Choosing which Angel token: every token with that name, by picture](docs/deck-token-versions.jpg)

![Mana curve and card types of a 100-card deck, with ramp, card draw, removal and board wipes](docs/deck-curve.jpg)

![Proxies: the cards a deck still misses at real card size, ready to print](docs/proxies.jpg)

![This week on the dashboard: the same summary goes to Discord every Monday](docs/dashboard-week.jpg)

![The game log: per deck how often it was played, its win rate and when it was last played](docs/games.jpg)

![The randomizer for three players, preferring decks that were not played for a while](docs/decks-random.jpg)

![Price rises in your collection: also sent to Discord after the daily price update](docs/dashboard-rises.jpg)

![A deck that follows its Archidekt link: Sync now, and when it was last checked](docs/deck-archidekt.jpg)

![The cards of a deck: the printing the deck list names (✓ when that is the one in the deck), a choice of printing where you own several, and Remove](docs/deck-cards.jpg)

![A deck taken over from Archidekt with its sets: every card uses the exact printing the deck list names](docs/deck-printings.jpg)

![Unsaved changes in a deck: the changed fields are highlighted and one button saves them all](docs/deck-unsaved.jpg)

![Searching inside a deck](docs/deck-search.jpg)

![A card taken out of a deck: it moved to Still missing and can be put back](docs/deck-removed.jpg)

![Deleting a deck asks for a second click instead of a pop-up](docs/deck-delete.jpg)

<sub>Destructive buttons use a two-click confirmation: the first click turns the button red ("Click again to delete"), a second click within four seconds confirms.</sub>

![Card detail page with price history chart](docs/card.jpg)

<sub>Screenshots use demo data: real cards and current prices, with a generated price history.</sub>

## Features

**Collection (HAVE)**
- Add cards by name (optionally a specific set, foil or non-foil), or by set + collector number to get one exact version when a set has several
- CSV import with automatic format detection (Archidekt, Moxfield, or any CSV with a quantity and name column); adds to your collection or replaces what earlier imports brought in. Cards you added by hand are kept; a card that is in the file too gets the file's quantity, and decks keep the printings you chose
- You decide which deck gets new cards: copies that come in through an import are not handed out to decks automatically. "Show the cards to sort" lists the new cards that a deck needs, with a "Put in this deck" button per deck (also under Still missing on the deck page), or let the app decide per card or for all of them
- Change the quantity of a card in place, without removing and re-adding it
- Tick or untick "foil" on a card you already have; price and totals follow, and it merges with the same card in the other finish
- See which decks a card is in: cards that are set aside for a deck carry an "in N decks" label that opens the list of decks, and how many copies are still free
- Show only the free copies, only the copies a deck uses, or only the free copies a deck still needs: cards you own that are missing from a deck carry a "needed by N decks" label with the decks
- Instant search in the collection on card name, set or deck (accents and word order do not matter), with a foil-only filter
- Versions of a card are grouped: one line per card with the number of versions, copies, price range and total value, which opens to show every version. Untick "Group versions" to see every version on its own line
- Spare copies: the copies no deck uses or needs (basic lands left out), with what they are worth together, to trade or sell. Sort the collection by value to see the most valuable ones first
- Filter the collection on colour (white, blue, black, red, green, colourless): cards with any of the chosen colours, cards that fit a deck of those colours, or cards with exactly those colours. Every card shows its colour identity
- A printing without a price borrows the price of the cheapest printing with the same name, marked "≈ SET"
- Daily price snapshots, total value over time, top 10 most valuable cards, weekly risers and fallers
- Price history chart per card
- Export the collection as a CSV in Moxfield's format (Moxfield, Archidekt and this app import it), and the wishlist with target and current prices
- A Discord message after the daily price update when cards you own went up in price (by default 25% and at least €1 in 7 days), with how many you have and how many are spare; the dashboard lists them too

**Wishlist (WANT)**
- Watch cards with a maximum price
- Periodic price check against Cardmarket, with Scryfall as a fallback
- Discord webhook alert when a price is at or below your target, at most once per card per 24 hours
- Choose the set you want a card from: "Change set" lists every printing with its price; the target price is kept
- Sort the wishlist by card name, current price, target price or difference, in either direction
- "Remove all" empties the wishlist in one go, after a second click to confirm
- Filter the wishlist on colour, like the collection; the 80–95% buttons then only change the cards you see
- Edit target prices freely and store them with one "Save all prices" button; set every target to 80, 85, 90 or 95% of the current price in one click
- Alert history

**Both**
- Fold away what you do not need: click the title of a part (say "Basic lands" or "Add a card") to fold it, click again to open it. It is remembered, and on deck pages it counts for every deck
- Collection value next to wishlist cost
- "Bought it" moves a card from WANT to HAVE
- Deck check: paste a decklist, upload a deck file or give an Archidekt URL. You see which cards you already own and in which printing (set, foil, quantity), so you can pull them from your collection, and you can add the missing cards to your wishlist in one click
- Saved decks: save a deck and the cards you own are set aside for it. Every copy can only be in one deck, so a second deck does not count the same Sol Ring again; it shows which deck is using it instead
- Basic lands and tokens are left out of the tracked cards by default, so they do not show up as missing or end up on your wishlist; tick a box to include them
- Deck size: basic lands count towards the cards of a deck, so you can see at a glance whether a Commander deck has exactly 100 cards, or how many it is short or over
- Basic lands per deck: fill in how many of each basic land a deck runs (shown as land cards), whatever the printing. Imported decks start with the numbers from the list, and older decks can move their lands over in one click
- Deck randomizer: can't decide what to play? Pick a random deck, or a different one for every player at the table, optionally only from one bracket or only decks you can build right now, and with a bigger chance for the decks you have not played for a while
- Tokens per deck: which tokens, emblems and helper cards (Morph, Manifest) a deck needs and how many (from the deck list, or added by hand), in alphabetical order with their pictures (click a number to change it) and tick boxes for packing them for game night. There are often several tokens with one name (many Angels): "other version" shows them all to pick from
- Mana curve and card types per deck: cards per mana value, the average mana value, how many creatures, instants, lands and so on, and how many cards ramp, draw, remove or wipe the board (guessed from the rules text)
- Proxies: a printable sheet of the cards a deck still misses (or the whole deck) at real card size, nine to an A4 page, for play-testing
- Export a deck as a text list for Archidekt or Moxfield, with the commander first and its tokens at the end
- Weekly summary on Discord (by default on Monday at 9:00): collection value and its change, new cards, risers and fallers, and the games you played. The dashboard shows the same under "This week"
- Commander picture: every deck shows its commander, taken from the Commander category on Archidekt, recognised from the deck name (only when that card can be a commander), or chosen on the deck page
- Game log: log a game from the Games page or from a deck (date, players, notes, won, lost or draw). Every deck shows how often it was played, its win rate and when it was last played
- Add cards to a saved deck the same way as to the collection: by name, or by set and card number for one exact version
- Take a card out of a deck: "Remove" moves it from "In this deck" to "Still missing" and frees your copies for other decks; "Put back" undoes it
- Search inside a deck on card name or set
- One "Save changes" button per deck: bracket, basic lands and chosen printings are edited freely and stored together, with a reminder while there are unsaved changes
- Commander bracket per deck: taken from Archidekt when the deck has one, and changeable at any time
- Filter the list of saved decks by bracket, or show only the decks that are complete
- "Has tokens" on a deck: a deck whose list has tokens is marked by itself, and you can tick or untick it on the deck page. The saved decks show a "tokens" badge and can be filtered on it, so you know which decks need tokens at the table
- Search the saved decks on deck name or on a card: "arcane signet" lists every deck that runs it, with the matching cards under the deck name
- Filter the saved decks on colour: every deck shows the colour identity of its cards, and you pick decks with exactly, any of, or within the chosen colours
- Sort the saved decks by how many cards they still miss, or by name, bracket, size, progress or value
- Follow an Archidekt deck: a deck saved from an Archidekt link (or given one later) is brought up to date when the deck changes there, automatically every few hours or with "Sync now"; printings you chose and cards you took out are kept
- The printing from the deck list comes along: an Archidekt deck (or a text list with sets) knows the set and card number of every card. If you own that printing it goes to that deck, also when another deck had taken it only because any copy would do, and missing cards go on the wishlist in that printing
- Choose the printing: if you own a card in several printings you pick which one is in the deck (say, the foil); otherwise the cheapest free copy is used
- Deck value: every saved deck shows what the copies in it are worth, and a chart of their value over time

## Quick start

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows; use `source .venv/bin/activate` elsewhere
pip install -r requirements.txt
copy .env.example .env          # optional: set DISCORD_WEBHOOK_URL
uvicorn app.main:app --reload
```

Open <http://localhost:8000> for the dashboard and <http://localhost:8000/docs>
for the interactive API docs.

Run the tests (no network needed, every external service is mocked):

```bash
pytest
```

## Configuration

All settings are environment variables (see [.env.example](.env.example)).

| Variable | Default | Meaning |
| --- | --- | --- |
| `DATABASE_URL` | `sqlite:///./mtg.db` | SQLModel/SQLAlchemy database URL |
| `DISCORD_WEBHOOK_URL` | – | Where alerts are posted; without it alerts are only logged in the app |
| `SCHEDULER_ENABLED` | `true` | Run the background jobs |
| `DAILY_UPDATE_HOUR` | `6` | Hour of the daily Scryfall price update |
| `WISHLIST_CHECK_HOURS` | `6` | Interval of the wishlist check |
| `DECK_SYNC_HOURS` | `6` | How often decks that follow an Archidekt link are brought up to date (`0` = never automatically) |
| `ALERT_COOLDOWN_HOURS` | `24` | Minimum time between two alerts for the same card |
| `PRICE_RISE_PCT` | `25` | Discord message when a card you own rose at least this many percent (`0` turns it off) |
| `PRICE_RISE_MIN_EUR` | `1` | ...and at least this many euros |
| `PRICE_RISE_DAYS` | `7` | ...compared with its price this many days ago; a card is mentioned once per period |
| `WEEKLY_SUMMARY_DAY` | `mon` | Day of the weekly Discord summary (`mon` … `sun`; empty turns it off) |
| `WEEKLY_SUMMARY_HOUR` | `9` | Hour of the weekly summary |
| `SCRYFALL_MIN_INTERVAL` | `0.1` | Seconds between Scryfall requests (their limit is 10/s) |
| `SCRYFALL_SLOW_INTERVAL` | `0.6` | Seconds between Scryfall name lookups and bulk requests, which have a much lower limit |
| `CARDMARKET_MIN_INTERVAL` | `5` | Seconds between Cardmarket requests (plus random jitter) |
| `CARDMARKET_ENABLED` | `true` | Set to `false` to skip scraping and use Scryfall prices only |

## A note on Cardmarket

Cardmarket has no public API, so the scraper reads the "From" price off the
product page. Two things to know:

- **The HTML changes regularly.** All selectors and labels live in
  [app/cardmarket_selectors.json](app/cardmarket_selectors.json), so a layout
  change is a config edit instead of a code change.
- **Cardmarket sits behind bot protection** and at the time of writing answers
  plain HTTP clients with `403`. The app does not try to get around that. When
  a scrape fails, the wishlist check falls back to Scryfall's EUR price, which
  is Cardmarket's own price trend updated once a day. After three refusals
  in a row the scraper leaves Cardmarket alone for an hour, so a long wishlist
  is checked in seconds instead of waiting out the rate limit for every card.
  Every check first fetches today's Scryfall prices for the whole wishlist in
  bulk. Alerts therefore keep
  working; they just react to the trend price rather than the cheapest listing.
  The source of every price is shown in the wishlist.

## API

| Method | Path | |
| --- | --- | --- |
| `GET` / `POST` | `/api/collection` | List the collection (`?q=` searches name and set, `?in_deck=false` keeps the cards that are in no deck yet, `?spare=true` the copies no deck uses or needs, `?colors=WU&color_match=any\|within\|exact` filters on colour) / add a card and fetch its price |
| `POST` | `/api/collection/import` | Upload an Archidekt or Moxfield CSV (`?replace=true` replaces the earlier imported cards, hand-added cards stay) |
| `PATCH` / `DELETE` | `/api/collection/{id}` | Change the quantity or the foil finish of an entry / remove it |
| `GET` / `POST` | `/api/wishlist` | List the wishlist (`?colors=WU&color_match=any\|within\|exact`) / watch a card with a max price |
| `POST` | `/api/wishlist/{id}/purchase` | Move a card from WANT to HAVE |
| `PUT` | `/api/wishlist/targets` | Set several target prices at once |
| `PUT` | `/api/wishlist/{id}/printing` | Watch another printing (set) of the same card |
| `GET` | `/api/printings?name=` | Every printing of a card with its price |
| `DELETE` | `/api/wishlist/{id}` | Stop watching a card |
| `DELETE` | `/api/wishlist` | Stop watching every card |
| `GET` | `/api/alerts` | Alert history |
| `GET` | `/api/cards/{id}/prices` | Price history of one card |
| `GET` | `/api/summary` | Collection value vs. wishlist cost |
| `GET` | `/api/summary/weekly` | The week in short, as sent to Discord |
| `GET` | `/api/tokens?name=` | The different tokens with a name, to choose from |
| `POST` | `/api/decks/check` | Have/missing overview for a decklist or Archidekt URL |
| `GET` / `POST` | `/api/decks` | List saved decks (`?q=` deck name or card, `?bracket=`, `?colors=WG&color_match=exact\|any\|within`) / save a deck and set its cards aside |
| `GET` | `/api/decks/random` | Pick random decks (`count`, `bracket`, `complete_only`) |
| `GET` / `DELETE` | `/api/decks/{id}` | A saved deck with its cards / delete it and free its cards |
| `PUT` | `/api/decks/{id}/cards/{entry_id}/printing` | Choose which printing a deck uses for a card |
| `POST` | `/api/decks/{id}/cards` | Add a card to a saved deck |
| `PUT` | `/api/decks/{id}/cards/{entry_id}/assigned` | Take your copies of a card out of a deck, or put them back |
| `POST` | `/api/decks/{id}/cards/{entry_id}/new-copy` | Put a new copy from an import, that waits to be sorted, in this deck |
| `POST` | `/api/collection/let-app-sort` | Let the app share out the new copies from imports over the decks (`?entry_id=` for one card) |
| `PUT` | `/api/decks/{id}/basic-lands` | Set how many of each basic land a deck runs |
| `PUT` | `/api/decks/{id}/tokens` | Mark a deck as one that has tokens, or not (`null` follows the deck list) |
| `PUT` | `/api/decks/{id}/commander` | Choose the commander of a deck (a card in it), or `null` for none |
| `GET` | `/api/decks/{id}/value-history` | What the copies in a deck were worth on every day with prices |
| `GET` / `POST` | `/api/games` | The game log (`?deck_id=` for one deck) / log a game |
| `DELETE` | `/api/games/{id}` | Remove a game from the log |
| `GET` | `/api/games/stats` | Per deck: games played, wins, losses, draws, win rate and last played |
| `PUT` | `/api/decks/{id}/bracket` | Set or clear the Commander bracket of a deck |
| `PUT` | `/api/decks/{id}/archidekt` | Let a deck follow an Archidekt deck, or stop following |
| `POST` | `/api/decks/{id}/sync`, `/api/decks/sync` | Bring one deck, or every linked deck, up to date with Archidekt |
| `POST` | `/api/jobs/update-prices`, `/api/jobs/check-wishlist` | Run a background job now |

## How it works

```
app/
  main.py              FastAPI app, lifespan (database + scheduler)
  models.py            Card, CollectionEntry, WishlistEntry, PriceSnapshot, Alert, DailyTotal, Deck, DeckEntry, DeckPick, DeckBasicLand, DeckSkipped
  scryfall.py          async Scryfall client (httpx), batch lookups, 429 retry
  cardmarket.py        product page scraper (BeautifulSoup), selectors from JSON
  rate_limit.py        one independent rate-limit queue per external source
  csv_import.py        CSV parsing and format detection
  discord.py           webhook alerts
  jobs.py              APScheduler jobs: daily prices, periodic wishlist check
  services/            collection, wishlist, pricing snapshots, dashboard stats, deck check
  routers/             JSON API (api.py) and Jinja2 pages (pages.py)
  templates/, static/  dashboard UI with Chart.js
tests/                 pytest suite: Scryfall client, scraper, alert logic, CSV import, API
```

- **Snapshot pattern** – prices are stored once per card, per day, per source
  (`PriceSnapshot`), and the collection/wishlist totals once per day
  (`DailyTotal`). Charts and "movers" are computed from those.
- **Missing prices** – some printings have no price on Scryfall. Such a card
  borrows the price of the cheapest printing with the same name that does have
  one, marked with "≈ SET" so you can see it is not its own price. If no
  printing has a price (most tokens), the card shows "–" and counts as €0 in
  totals instead of breaking them.
- **Card names** – names like `Lim-Dûl's Vault` and `Fire // Ice` are covered
  by tests end to end (lookup, CSV import, deck check).

## Tech stack

FastAPI · SQLModel + SQLite · httpx · BeautifulSoup4 · APScheduler · Discord
webhooks · Jinja2 · Chart.js · pytest

Card data and prices by [Scryfall](https://scryfall.com). Not affiliated with
Wizards of the Coast, Scryfall, Cardmarket or Archidekt.
