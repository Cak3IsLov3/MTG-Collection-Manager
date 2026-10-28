# MTG Collection Manager

One app for your Magic: The Gathering finances: track what your collection is
worth over time **and** get a Discord alert when a card you want drops to your
price.

- **HAVE** – your collection, valued daily with Scryfall prices
- **WANT** – your wishlist, watched against Cardmarket prices
- **PLAY** – your decks, game nights and a token overlay for your stream

> "Your collection is worth €850, your wishlist still costs €120."

![Dashboard: a banner with the deck you played last, your week and your newest achievement; collection value, wishlist cost and what you paid, and tabs for the value over time, the top cards and movers, alerts and backups](docs/dashboard.jpg)

![The Colours tab: how your collection is spread over the five colours, with the weakest and the strongest one](docs/dashboard-colours.jpg)

| Top cards and weekly movers | Wishlist with targets, sorted by current price |
| --- | --- |
| ![The "Top cards & movers" tab: the 10 most valuable cards, with risers and fallers below](docs/dashboard-cards.jpg) | ![The wishlist: the cards you watch, with tabs to add a card and for the alert history](docs/wishlist.jpg) |

| Collection: add by name or card number, edit quantities, tick foil | Saved decks with their commander, colours, games played and value |
| --- | --- |
| ![The collection: your cards with the symbol of their set in the colour of their rarity, with adding and importing in their own tab](docs/collection.jpg) | ![Saved decks, each with a stripe and a ring in its colours, with their commander, bracket, progress, value and how often they were played](docs/decks.jpg) |

| Search everything (press `/`): cards, decks and wishlist | Point at a card name to see the card |
| --- | --- |
| ![Searching "sol": Sol Ring in your collection and every deck that runs it](docs/search.jpg) | ![The card picture next to the mouse when you point at a card name](docs/card-preview.jpg) |

![Game nights: per day the games, who won, your decks, who was at the table and their commanders, with a recap for Discord](docs/games-nights.jpg)

![Achievements from your game log: unlocked ones in gold with their date, the others with how far you are](docs/games-achievements.jpg)

### Stream overlay for OBS

Press **▶ Play** on a deck and the stream screen opens with every token that deck makes ready to put on the board, while the deck intro slides in on stream. See [docs/stream.md](docs/stream.md).

![The stream control panel after Play: the tokens of the deck, emblems included, each with the cards that make it](docs/stream-tokens.jpg)

| The board: counters, mana, dice and every stack | The overlay in OBS (shown here on a checkered background) |
| --- | --- |
| ![The Board tab of the stream control panel: the stacks, and under Show on stream the deck intro, commander tax and storm, and the spotlight](docs/stream-board.jpg) | ![The overlay: the token stacks with counts and power/toughness, the mana pool and the turn tracker](docs/stream-overlay.jpg) |

| The deck intro: commander, deck, bracket and record | A card in the spotlight, with commander tax and storm at the bottom |
| --- | --- |
| ![The deck intro on stream: Kess art, "Now playing Grixis Storm", Bracket 4 and 4 W · 3 L](docs/stream-intro.jpg) | ![Rhystic Study big in the middle of the stream; commander tax +4 and storm 6 next to the turn tracker](docs/stream-spotlight.jpg) |

![Victory! in gold with confetti over the stream, with the deck and its new record](docs/stream-victory.jpg)

| The playtest page: your hand is only here | What the stream shows: no hand, only the battlefield |
| --- | --- |
| ![The playtest page: creatures, other permanents and lands on the battlefield with four Islands in one stack, the hand below, and the library, graveyard, exile and command zone](docs/playtest.jpg) | ![The overlay with the playtest table: the battlefield and your life, and only how many cards you hold](docs/playtest-overlay.jpg) |

| The Stream Deck page: 32 keys, row by row | A ready-made profile, or one key at a time |
| --- | --- |
| ![The Stream Deck page: the 32 keys of a Stream Deck XL with their pictures](docs/streamdeck.jpg) | ![Download a ready-made Stream Deck profile, or set up keys with the Website action](docs/streamdeck-profile.jpg) |

![Wishlist targets set to 85% of the current price: the changed prices are highlighted until you save them all](docs/wishlist-targets.jpg)

![The wishlist filtered on colour with the mana symbols: the cards with red or green, each with its colours](docs/wishlist-colors.jpg)

![Wishlist cards that got 26% cheaper in a week are marked, also when they are not at the target yet; "Bought it" takes what you paid](docs/wishlist-drops.jpg)

![Choosing the set of a wishlist card: every printing of Force of Will with its price](docs/wishlist-set.jpg)

![Remove all asks for a second click before it empties the wishlist](docs/wishlist-remove-all.jpg)

![Importing a CSV with "Replace my imported cards": the cards added by hand are kept](docs/collection-import.jpg)

![After an import the new cards wait for you: no deck gets them until you put them in one](docs/collection-new-cards.jpg)

![Sorting the new cards: every deck that needs a card, with a "Put in this deck" button, or let the app decide](docs/collection-sort.jpg)

![On the deck page a missing card with a new copy waiting can be put in the deck directly](docs/deck-new-copy.jpg)

![Searching the collection: Sol Ring is one line with its five versions opened underneath, told apart by set and card number](docs/collection-search.jpg)

![Spare copies sorted by value: what no deck uses or needs, and what it is worth together](docs/collection-spare.jpg)

![Which decks a card is in: the label opened for two Sol Ring printings](docs/collection-decks.jpg)

![Filtering on colour with the mana symbols: the cards that fit a blue-black deck, each with its colour identity](docs/collection-colors.jpg)

![Free copies a deck still needs: cards you own that are missing from a deck, with the decks that need them](docs/collection-needed.jpg)

![Only the free copies: a card with copies in decks shows how many of its copies are free, and the total is what those are worth](docs/collection-free.jpg)

![What you paid per card, filled in right in the list, or per copy when you bought them apart, with what each card gained or lost since and the total for all of them](docs/collection-paid.jpg)

![Printings without a price of their own borrow one from another printing, marked ≈ SET (Dragon Egg and Fellwar Stone)](docs/collection-borrowed.jpg)

![Searching the saved decks for a card: every deck that runs Sol Ring, with the matching card under its name](docs/decks-search.jpg)

![Only the decks whose tokens you own: the decks marked "tokens owned", ready to play](docs/decks-tokens.jpg)

![The saved decks filtered on bracket 3: two of the six decks are left](docs/decks-filter.jpg)

![The saved decks filtered on colour: the decks with green in them, each with its colours](docs/decks-colors.jpg)

![Colour groups: every deck shows the name of its colours (Azorius, Grixis, Selesnya...), and the groups you have no deck in yet are listed above the decks; all 32 groups with the number of decks in each can be opened, and the list can be filtered and sorted on colour group](docs/decks-colour-groups.jpg)

![A colour group you do not play yet (Dimir): the commanders you own in exactly those colours that no deck uses, and the most played ones, each with how many of its 60 most played cards you own](docs/colour-group.jpg)

![The saved decks sorted on missing cards: the decks closest to done first](docs/decks-sorted.jpg)

![Folders for decks: a Folder column with a menu per deck, a filter on folder, and the Folders panel below the list to make, rename and delete them; decks marked as testing decks show a badge](docs/decks-folders.jpg)

![A testing deck: the Testing deck box and the Folder menu next to the bracket; the deck takes no cards from your collection, so all 64 cards count as missing and it is worth 0 euros](docs/deck-testing.jpg)

![Adding a deck as a testing deck: tick Testing deck next to Include tokens before saving](docs/deck-add-testing.jpg)

![A saved deck: its commander, bracket and "Tokens owned", an overview of cards, value, deck size and games, and tabs for its cards, analysis, improvements, lands and tokens, games and value, and Archidekt](docs/deck.jpg)

![The cards of a deck as pictures: a stack per card type, missing cards grey, a foil copy shining](docs/deck-wall.jpg)

![The Analysis tab of a deck: the mana curve with the cards under every bar, a ring with the colours of the deck and its card types, then the bracket check](docs/deck-analysis.jpg)

![A bar of the mana curve clicked open: the cards with that mana value as pictures](docs/deck-curve.jpg)

![The mana base: sources per colour against what the hardest spell needs, the chance of enough lands on turn 3, 4 and 5, and the salt score of the deck with its saltiest cards](docs/deck-mana.jpg)

![Logging a game from the deck page, and the value of the deck's copies over the last month](docs/deck-value.jpg)

![The tokens of a deck in one panel: the token list with how many the deck needs of each (Soldier: 2, needs about 5), emblems included, and the tokens the deck makes that are not in the list yet](docs/deck-tokens.jpg)

![Parts of a deck folded away by clicking their title: only what you need stays open](docs/deck-folded.jpg)

![Choosing which Angel token: every token with that name, by picture](docs/deck-token-versions.jpg)

![The bracket check: eight Game Changers do not fit bracket 3; below it the combos in the deck and the ones it is one card away from, from Commander Spellbook](docs/deck-bracket.jpg)

![The deck next to EDHREC: the cards other Marwyn decks play most, how often, their synergy, and which ones you have a free copy of](docs/deck-edhrec.jpg)

![EDHREC with "Only cards I have free": the popular cards you could put in the deck right away](docs/deck-edhrec-free.jpg)

![The weakest cards of a deck: the cards the fewest decks with its commander play, each with a more popular card of the same kind from your collection to swap in](docs/deck-weakest.jpg)

![Weakest cards with "Only cards I have free": only the weak cards you have a better card for in your collection](docs/deck-weakest-free.jpg)

![Typing ahead: card names from Scryfall as you type, with a picture of the highlighted one](docs/autocomplete.jpg)

![Suggestions from your collection: free cards in the deck's colours, grouped by what the deck is short of](docs/deck-suggestions.jpg)

![Adding a token: while you type, the app says how many the deck needs and fills that in](docs/deck-token-suggestions.jpg)

![Basic lands shown as land cards, and the token list with the number per token as text that turns into a field when clicked](docs/deck-lands.jpg)

![Proxies: the cards a deck still misses at real card size, ready to print](docs/proxies.jpg)

![This week on the dashboard: the same summary goes to Discord every Monday](docs/dashboard-week.jpg)

![The live table on an iPad: life, poison and commander damage per player, whose turn it is, and the top row upside down for the players across the table](docs/table.jpg)

![Starting a game on the live table: your deck, the number of players, starting life, and the names and commanders of the others](docs/table-setup.jpg)

![The Games page: log a game, then tabs for the overview (win rate, streak, nemesis, games per month), opponents, per deck and the log](docs/games.jpg)

![Win rates by table size, deck colour and bracket, and per opponent; the player who beats you most is your nemesis](docs/games-stats.jpg)

![How games are won: combat, commander damage, combo and the rest, how often you won and lost to each, and your win rate when you go first](docs/games-wins.jpg)

![The commanders each opponent plays, and your results against every commander](docs/games-commanders.jpg)

![The randomizer for three players, preferring decks that were not played for a while and skipping the decks played tonight](docs/decks-random.jpg)

![Price rises in your collection: also sent to Discord after the daily price update](docs/dashboard-rises.jpg)

![The Alerts tab of the dashboard: reprints of valuable cards you own, next to wishlist targets, price drops and price rises; all of them also go to Discord](docs/dashboard-reprints.jpg)

![A deck that follows its Archidekt link: Sync now, and when it was last checked](docs/deck-archidekt.jpg)

![The cards of a deck: the printing the deck list names (✓ when that is the one in the deck), a choice of printing where you own several, and Remove](docs/deck-cards.jpg)

![A deck taken over from Archidekt with its sets: every card uses the exact printing the deck list names](docs/deck-printings.jpg)

![Unsaved changes in a deck: the changed fields are highlighted and one button saves them all](docs/deck-unsaved.jpg)

![Searching inside a deck](docs/deck-search.jpg)

![A card taken out of a deck: it moved to Still missing and can be put back](docs/deck-removed.jpg)

![Add all cards to this deck under Still missing: the first click says how many cards go into your collection, the second click does it](docs/deck-add-all.jpg)

![Deleting a deck asks for a second click instead of a pop-up](docs/deck-delete.jpg)

<sub>Destructive buttons use a two-click confirmation: the first click turns the button red ("Click again to delete"), a second click within four seconds confirms.</sub>

![A card page: its art as a banner, the versions you own and the price history](docs/card.jpg)

<sub>Screenshots use demo data: real cards and current prices, with a generated price history.</sub>

## Features

**Collection (HAVE)**
- Add cards by name (optionally a specific set, foil or non-foil), or by set + collector number to get one exact version when a set has several
- CSV import with automatic format detection (Archidekt, Moxfield, or any CSV with a quantity and name column); adds to your collection or replaces what earlier imports brought in. Cards you added by hand are kept; a card that is in the file too gets the file's quantity, and decks keep the printings you chose
- You decide which deck gets new cards: copies that come in through an import are not handed out to decks automatically. "Show the cards to sort" lists the new cards that a deck needs, with a "Put in this deck" button per deck (also under Still missing on the deck page), or let the app decide per card or for all of them. Only the latest import is new: with the next import, the copies still waiting from the one before are handed out to the decks as usual
- Change the quantity of a card in place, without removing and re-adding it
- Tick or untick "foil" on a card you already have; price and totals follow, and it merges with the same card in the other finish
- See which decks a card is in: cards that are set aside for a deck carry an "in N decks" label that opens the list of decks, and how many copies are still free
- Show only the free copies, only the copies a deck uses, or only the free copies a deck still needs: cards you own that are missing from a deck carry a "needed by N decks" label with the decks. Every card then shows how many of its copies are in view ("2 free"), and the total is what those copies are worth
- Purchase price: fill in what you paid per card (in the list, when adding a card, on "Bought it", or from the "Purchase Price" column of a CSV), and see what every card gained or lost since, and in total. Buying more copies keeps the average price
- Instant search in the collection on card name, set or deck (accents and word order do not matter), with a foil-only filter
- Versions of a card are grouped: one line per card with the number of versions, copies, price range and total value, which opens to show every version. Untick "Group versions" to see every version on its own line
- Spare copies: the copies no deck uses or needs (basic lands left out), with what they are worth together, to trade or sell. Sort the collection by value to see the most valuable ones first
- Filter the collection on colour (white, blue, black, red, green, colourless): cards with any of the chosen colours, cards that fit a deck of those colours, or cards with exactly those colours. Every card shows its colour identity as mana symbols
- A printing without a price borrows the price of the cheapest printing with the same name, marked "≈ SET"
- Daily price snapshots, total value over time, top 10 most valuable cards, weekly risers and fallers
- Price history chart per card
- Export the collection as a CSV in Moxfield's format (Moxfield, Archidekt and this app import it), and the wishlist with target and current prices
- A Discord message after the daily price update when cards you own went up in price (by default 25% and at least €1 in 7 days), with how many you have and how many are spare; the dashboard lists them too
- Reprint alert: when a card you own worth €5 or more gets a new printing, also one that is only announced, you get a Discord message with the sets and release dates (a reprint often makes the price drop). Every card and set is mentioned once
- Daily backup of the database (by default at 3:00, or when the app starts and there is none of that day yet); the newest 14 are kept in `backups/`. The dashboard shows the last one and has a "Back up now" button

**Wishlist (WANT)**
- Watch cards with a maximum price
- Periodic price check against Cardmarket, with Scryfall as a fallback
- Discord webhook alert when a price is at or below your target, at most once per card per 24 hours
- Price drop alert: a wishlist card that got 15% or more (and at least €0.50) cheaper in 7 days is marked "cheaper" and sent to Discord once, also when it is not at your target yet
- Choose the set you want a card from: "Change set" lists every printing with its price; the target price is kept
- Sort the wishlist by card name, current price, target price or difference, in either direction
- "Remove all" empties the wishlist in one go, after a second click to confirm
- Filter the wishlist on colour, like the collection; the 80–95% buttons then only change the cards you see
- Edit target prices freely and store them with one "Save all prices" button; set every target to 80, 85, 90 or 95% of the current price in one click
- Alert history

**Both**
- Tabs on every page: what you always need stays on top, the rest is in tabs (with counts such as "80 missing" or "⚠ bracket"). The tab bar stays in view while you scroll, the tab you chose is remembered, and a link like `/decks/3#improve` opens that tab
- Search everything from the menu (press `/`): your cards, decks (by name or a card in them) and wishlist as you type
- Point at a card name anywhere to see the card; the deck page has the commander's art as a banner and a mana curve in the colours of the cards, with the cards under every bar; sets are coloured by rarity and brackets by level
- Dashboard banner: the deck you played last with its commander art, your week (wins and losses) and your newest achievement
- Achievements from your game log: First blood, Ten wins, On a roll, Clean sweep, Big table, All five colours and more, plus per deck First win, Ten wins and Hat-trick (shown on the deck page). Unlocked with I won on the stream, they show on stream too
- The cards of a deck as pictures: switch the list to a stack of card pictures per type, like on a table; missing cards are grey, foil copies shine
- Foil copies shine: a moving rainbow sheen on foil cards in the collection, on the card page and in decks
- The app's own icons (no emoji), soft transitions between pages and tabs
- Set symbols: the symbol of each card's set, in the colour of its rarity (common, uncommon, rare, mythic), in the collection, the wishlist and on the card page
- A price for every copy: bought four Command Towers apart? Fill in what each one cost ("per copy" under Paid); the field shows the average and the gain counts every copy
- A long collection loads fast: the first 150 cards show at once and the next ones as you scroll, while search, filters and totals cover all of them
- Card page: the art as a banner, every version you own as a row of pictures, and a price chart with a soft fill
- Game nights: the games of a day together (who won, your decks, the table and their commanders), with a recap to Discord the morning after (GAME_NIGHT_RECAP)
- Fold away what you do not need: click the title of a part (say "Basic lands" or "Add a card") to fold it, click again to open it. It is remembered, and on deck pages it counts for every deck
- Collection value next to wishlist cost
- "Bought it" moves a card from WANT to HAVE
- Deck check: paste a decklist, upload a deck file or give an Archidekt URL. You see which cards you already own and in which printing (set, foil, quantity), so you can pull them from your collection, and you can add the missing cards to your wishlist in one click
- Saved decks: save a deck and the cards you own are set aside for it. Every copy can only be in one deck, so a second deck does not count the same Sol Ring again; it shows which deck is using it instead
- Basic lands and tokens are left out of the tracked cards by default, so they do not show up as missing or end up on your wishlist; tick a box to include them
- Deck size: basic lands count towards the cards of a deck, so you can see at a glance whether a Commander deck has exactly 100 cards, or how many it is short or over
- Basic lands per deck: fill in how many of each basic land a deck runs (shown as land cards), whatever the printing. Imported decks start with the numbers from the list, and older decks can move their lands over in one click
- Deck randomizer: can't decide what to play? Pick a random deck, or a different one for every player at the table, optionally only from one bracket or only decks you can build right now, with a bigger chance for the decks you have not played for a while, and skipping the decks you already played tonight
- Tokens per deck: which tokens, emblems and helper cards (Morph, Manifest, On an Adventure) a deck needs and how many (from the deck list, or added by hand), in alphabetical order with their pictures (click a number to change it) and tick boxes for packing them for game night. There are often several tokens with one name (many Angels): "other version" shows them all to pick from. Double-faced tokens show both sides as their own picture, and a picture with two tokens on it (the Roles of Wilds of Eldraine: Monster // Sorcerer) is cut in two, the second one turned the right way up; "Top token only" keeps just the top one. "Print tokens" makes a sheet of every token, as many as you bring, at real card width
- Mana curve and card types per deck: cards per mana value with the cards as pictures under every bar (click a bar to see them), the average mana value, a ring with the colours of the deck, how many creatures, instants, lands and so on, and how many cards ramp, draw, remove or wipe the board (guessed from the rules text)
- Mana base check per deck: the sources of every colour (lands, fetch lands, and mana rocks or dorks up to two mana) against what the hardest spell of that colour needs for an 85% chance on its turn (Frank Karsten's method), the chance of two lands in your opening hand and of three, four and five lands by turn three, four and five, and advice when a colour is short
- Salt score per deck, from EDHREC's salt scores per card (how much players hate playing against it): one score for the deck with a label from Sweet to Very salty, and its saltiest cards
- Proxies: a printable sheet of the cards a deck still misses (or the whole deck) at real card size, nine to an A4 page, for play-testing
- Export a deck as a text list for Archidekt or Moxfield, with the commander first and its tokens at the end
- Weekly summary on Discord (by default on Monday at 9:00): collection value and its change, new cards, risers and fallers, and the games you played. The dashboard shows the same under "This week"
- Commander picture: every deck shows its commander, taken from the Commander category on Archidekt, recognised from the deck name (only when that card can be a commander), or chosen on the deck page
- Playtest on stream: a page of its own, like the Archidekt playtester, where you shuffle a deck, draw seven and play it with a hidden hand, while the stream overlay (your virtual camera in Convoke, SpellTable or Discord) shows only the battlefield, graveyard, exile, command zone and life. Cards you do not own count too. See [docs/stream.md](docs/stream.md)
- Game log: log a game from the Games page or from a deck (date, players, who you played against and their commanders, who won, how it ended, who went first, notes). Every deck shows how often it was played, its win rate and when it was last played
- Live table for game night (made for an iPad on the table): life, poison and commander damage per player, whose turn it is and the round, undo, full screen, and the screen stays on. Players are out at 0 life, 10 poison or 21 commander damage; the top row can be turned upside down for the players across. At the end you pick the winner and the game goes into the log with everyone and their commanders. A game in progress survives a reload
- **Stream overlay for OBS**: show tokens, counters, mana, dice and a turn tracker on your stream, with a control panel to run it (also as an OBS dock). Take the tokens of any saved deck with one click (**▶ Play** on a deck; two tokens on one picture, like the Roles, become two tokens, and on stream only their name bar shows, like the Ring emblem, with a button per token for the whole token), with a deck intro on stream (commander, bracket and record). Put any card big in the spotlight, keep track of commander tax and storm, and end with Victory! with confetti and cards falling in the colours of your deck (the game goes straight into your log), tonight's score in view and achievements as they unlock. A ready-made Stream Deck profile (32 keys, with pictures) to import with a double-click; it works without a plugin. Control panel at `/stream/control`, Browser source at `/stream/overlay`; see [docs/stream.md](docs/stream.md)
- The commanders of your opponents: fill them in when you log a game (the one a player used last time is filled in). The Games page shows the commanders each player plays and your results against every commander
- Weakest cards per deck: the cards the fewest decks with its commander play on EDHREC (under 10%), each with a more popular card of the same kind from your collection to swap in. "Only cards I have free" shows just the ones you can swap right away
- Game statistics: your win rate, current streak and longest winning streak, games per month, win rates by table size (next to your fair share), deck colour and bracket, and per opponent, with the player who beats you most as your nemesis
- How games are won: for every game you can fill in how it ended (combat, commander damage, combo, drain, poison, alternate win or mill, the others conceded) and who went first, also afterwards in the log. The Games page shows how often each way won or beat you, and your win rate when you go first; a deck shows how it wins most. The live table remembers who went first
- Bracket check per deck: Game Changers (the official list, from Scryfall), mass land denial, extra turns and two-card infinite combos, against the bracket you set. A deck that does not fit gets a ⚠ in the list of decks
- Combos per deck from Commander Spellbook: the combos in the deck and what they do, and the combos it is one card away from, marked when you own that card
- EDHREC next to every deck with a commander: the cards other decks with that commander play most, how often and their synergy, with the ones you have a free copy of marked
- Suggestions from your collection: free cards in the colours of a deck that are not in it yet, grouped by what the deck is short of (ramp, card draw, removal, board wipes), with an "Add to deck" button
- Token suggestions in the same panel as the token list: every token shows about how many the cards of the deck make (from Scryfall, emblems included), tokens the deck makes but the list misses come after it, with buttons to add them or raise the number. Typing a token name says how many the deck needs and fills that in. Tokens you always bring (Clue and Copy by default) are left out
- A Discord message when the automatic sync finds changes in a deck on Archidekt: what was added, removed and changed, and which new cards you still need
- Add cards to a saved deck the same way as to the collection: by name, or by set and card number for one exact version
- Take a card out of a deck: "Remove" moves it from "In this deck" to "Still missing" and frees your copies for other decks; "Put back" undoes it
- Search inside a deck on card name or set
- Typing ahead everywhere you enter a card name: names from Scryfall with a picture of the highlighted card; tokens in the token field, only commanders for the commanders of opponents
- One "Save changes" button per deck: bracket, basic lands and chosen printings are edited freely and stored together, with a reminder while there are unsaved changes
- Commander bracket per deck: taken from Archidekt when the deck has one, and changeable at any time
- Filter the list of saved decks by bracket, or show only the decks that are complete
- "Tokens owned" on a deck: tick it when you have the tokens the deck needs. Until you set it, a deck whose list has tokens counts as ticked; adding tokens to its list never ticks it for you. The saved decks show a "tokens owned" badge and can be filtered on it, so you know which decks are ready to play
- Folders for decks: make them under the list of decks ("Folders"), put a deck in one with the menu in its row (or on its own page) and filter the list on a folder. Deleting a folder keeps its decks
- "Testing deck" on a deck: tick it when you only want to try the deck out. It takes no copies from your collection, so every card counts as missing (the Proxies sheet prints them) and your real decks keep their cards
- "Add all cards to this deck" under Still missing: puts one copy of every missing card in your collection (after a second click), so the deck is complete. Cards you took out come back and new copies from an import that wait go in first. Not on testing decks. Also ticking "Testing deck" when you add a deck makes it a testing deck from the start
- Search the saved decks on deck name or on a card: "arcane signet" lists every deck that runs it, with the matching cards under the deck name
- Filter the saved decks on colour: every deck shows the colour identity of its cards, and you pick decks with exactly, any of, or within the chosen colours
- Sort the saved decks by how many cards they still miss, or by name, bracket, size, progress or value
- Follow an Archidekt deck: a deck saved from an Archidekt link (or given one later) is brought up to date when the deck changes there, automatically every few hours or with "Sync now"; printings you chose and cards you took out are kept
- The printing from the deck list comes along: an Archidekt deck (or a text list with sets) knows the set and card number of every card. If you own that printing it goes to that deck, also when another deck had taken it only because any copy would do, and missing cards go on the wishlist in that printing
- Choose the printing: if you own a card in several printings you pick which one is in the deck (say, the foil); otherwise the cheapest free copy is used
- Colour groups: every deck shows the name of its colours (Selesnya, Jund, Mono-blue, Glint-Eye...), and the deck list shows which of the 32 colour groups you have no deck in yet. A click on a group opens two lists: the commanders you own in exactly those colours that no deck uses yet, each with a button **Build a deck from my cards** that makes a deck of that commander and your free cards its decks play most on EDHREC (lands left out, they are for you to add), and the commanders that most decks play, each with how many of their 60 most played cards you already own (EDHREC). The deck list can be filtered and sorted on colour group, and the dashboard has a Colours tab with how your whole collection is spread over the five colours and in which you are weakest
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

### As a program (.exe)

On Windows you can build one file that starts the server and opens your browser, so you do not need Python to run it:

```bash
build_exe.bat
```

This makes `dist\MTGCollectionManager.exe`. Put it in a folder of its own, or next to your existing `mtg.db`, and start it:
the collection, `.env`, backups and the stream files (`stream-state*.json`) are kept in the folder of the .exe, so moving
the folder moves everything. Closing its window stops the program. It uses port 8017 (set `PORT` to change it); starting it a
second time just opens the one that is already running.

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
| `PRICE_DROP_PCT` | `15` | Discord message when a wishlist card got at least this many percent cheaper (`0` turns it off) |
| `PRICE_DROP_MIN_EUR` | `0.5` | ...and at least this many euros |
| `PRICE_DROP_DAYS` | `7` | ...compared with its price this many days ago; a card is mentioned once per period |
| `REPRINT_MIN_EUR` | `5` | Discord message when a card you own worth at least this much gets a new printing (`0` turns it off) |
| `REPRINT_DAYS` | `60` | ...looking at printings released or announced in the last this many days |
| `DECK_CHANGE_ALERTS` | `true` | Discord message when the automatic sync finds changes in a deck on Archidekt |
| `STANDARD_TOKENS` | `Clue,Copy` | Tokens you always bring along; the token suggestions leave them out |
| `BACKUP_DIR` | `backups` | Folder of the daily database backups |
| `BACKUP_KEEP` | `14` | How many backups are kept (`0` turns backups off) |
| `BACKUP_HOUR` | `3` | Hour of the daily backup |
| `WEEKLY_SUMMARY_DAY` | `mon` | Day of the weekly Discord summary (`mon` … `sun`; `daily` for a summary of the last day every day; empty turns it off) |
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
| `PATCH` / `DELETE` | `/api/collection/{id}` | Change the quantity, the foil finish or the purchase price of an entry / remove it |
| `GET` / `POST` | `/api/wishlist` | List the wishlist (`?colors=WU&color_match=any\|within\|exact`) / watch a card with a max price |
| `POST` | `/api/wishlist/{id}/purchase` | Move a card from WANT to HAVE |
| `PUT` | `/api/wishlist/targets` | Set several target prices at once |
| `PUT` | `/api/wishlist/{id}/printing` | Watch another printing (set) of the same card |
| `GET` | `/api/printings?name=` | Every printing of a card with its price |
| `GET` | `/api/autocomplete?q=` | Card names for typing ahead (`kind=card\|token\|commander`) |
| `GET` | `/api/decks/{id}/weakest` | The cards the fewest decks with the commander play, with a card to swap in |
| `DELETE` | `/api/wishlist/{id}` | Stop watching a card |
| `DELETE` | `/api/wishlist` | Stop watching every card |
| `GET` | `/api/alerts` | Alert history |
| `GET` | `/api/cards/{id}/prices` | Price history of one card |
| `GET` | `/api/summary` | Collection value vs. wishlist cost |
| `GET` | `/api/summary/weekly` | The week in short, as sent to Discord |
| `GET` | `/api/tokens?name=` | The different tokens with a name, to choose from |
| `POST` | `/api/decks/check` | Have/missing overview for a decklist or Archidekt URL |
| `GET` / `POST` | `/api/decks` | List saved decks (`?q=` deck name or card, `?bracket=`, `?colors=WG&color_match=exact\|any\|within`) / save a deck and set its cards aside |
| `GET` | `/api/decks/random` | Pick random decks (`count`, `bracket`, `complete_only`, `skip_played_tonight`) |
| `GET` / `DELETE` | `/api/decks/{id}` | A saved deck with its cards / delete it and free its cards |
| `PUT` | `/api/decks/{id}/cards/{entry_id}/printing` | Choose which printing a deck uses for a card |
| `POST` | `/api/decks/{id}/cards` | Add a card to a saved deck |
| `PUT` | `/api/decks/{id}/cards/{entry_id}/assigned` | Take your copies of a card out of a deck, or put them back |
| `POST` | `/api/decks/{id}/cards/{entry_id}/new-copy` | Put a new copy from an import, that waits to be sorted, in this deck |
| `POST` | `/api/collection/let-app-sort` | Let the app share out the new copies from imports over the decks (`?entry_id=` for one card) |
| `PUT` | `/api/decks/{id}/basic-lands` | Set how many of each basic land a deck runs |
| `PUT` | `/api/decks/{id}/tokens` | Mark that you own the tokens of a deck, or not (`null` follows the deck list) |
| `PUT` | `/api/decks/{id}/commander` | Choose the commander of a deck (a card in it), or `null` for none |
| `POST` | `/api/decks/{id}/stream` | Put the tokens of a deck in the stream control panel |
| `POST` | `/api/decks/{id}/stream/intro` | Show the deck intro on stream, with the record as it is now |
| `GET` | `/api/decks/{id}/value-history` | What the copies in a deck were worth on every day with prices |
| `GET` / `POST` | `/api/games` | The game log (`?deck_id=` for one deck) / log a game |
| `PATCH` | `/api/games/{id}` | Fill in afterwards how a game was won (`win_method`: `combat`, `commander`, `combo`, `drain`, `poison`, `alt`, `concede`, `other`) and whether you went first (`went_first`) |
| `DELETE` | `/api/games/{id}` | Remove a game from the log |
| `GET` | `/api/games/stats` | Per deck: games played, wins, losses, draws, win rate and last played |
| `GET` | `/api/games/overview` | Win rate, streaks, and win rates per month, table size, colour, bracket and opponent |
| `GET` | `/api/decks/{id}/bracket-check` | Game Changers, mass land denial, extra turns and two-card combos against the deck's bracket |
| `GET` | `/api/decks/{id}/combos` | The combos in a deck and the ones it is one card away from (Commander Spellbook) |
| `GET` | `/api/decks/{id}/edhrec` | EDHREC's most played cards for the deck's commander, next to the deck and your collection |
| `GET` | `/api/decks/{id}/suggestions` | Free cards from your collection that fit the deck |
| `GET` / `POST` | `/api/decks/{id}/token-suggestions` | The tokens the deck's cards make and how many / take them over (`?scryfall_id=` for one) |
| `GET` | `/api/reprints` | Reprints found of valuable cards you own |
| `POST` | `/api/backup` | Back up the database now |
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
  spellbook.py         Commander Spellbook client: the combos in a deck
  edhrec.py            EDHREC client: the most played cards per commander, and the salt scores
  backup.py            daily copies of the SQLite database
  stream/              the token overlay for OBS, mounted at /stream (see docs/stream.md)
  rate_limit.py        one independent rate-limit queue per external source
  csv_import.py        CSV parsing and format detection
  discord.py           webhook alerts
  jobs.py              APScheduler jobs: daily prices, wishlist check, deck sync, backups, Discord messages
  services/            collection, wishlist, pricing snapshots, dashboard stats, decks, games, combos,
                       suggestions, EDHREC comparison, reprints
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

Card data and prices by [Scryfall](https://scryfall.com), combos by
[Commander Spellbook](https://commanderspellbook.com), popular cards per commander by
[EDHREC](https://edhrec.com). Not affiliated with Wizards of the Coast, Scryfall,
Cardmarket, Archidekt, Commander Spellbook or EDHREC.
