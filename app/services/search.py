"""One search over everything: the cards you own, your decks (by name or by a card in them) and your
wishlist."""

from sqlmodel import Session

from app.services.collection import _fold, collection_rows
from app.services.decks import deck_card_names, deck_summaries
from app.services.wishlist import wishlist_rows

PER_GROUP = 6


def _matches(text: str, words: list[str]) -> bool:
    folded = _fold(text)
    return all(word in folded for word in words)


def search(session: Session, query: str) -> dict:
    words = _fold(query or "").split()
    if not words or len(" ".join(words)) < 2:
        return {"query": query, "cards": [], "decks": [], "wishlist": []}

    cards: dict[str, dict] = {}
    for row in collection_rows(session):
        if not _matches(row.card.name, words):
            continue
        card = cards.setdefault(row.card.name, {
            "name": row.card.name, "card_id": row.card.id, "image_url": row.card.image_url, "copies": 0, "versions": 0,
        })
        card["copies"] += row.entry.quantity
        card["versions"] += 1
        card["image_url"] = card["image_url"] or row.card.image_url
    found_cards = sorted(cards.values(), key=lambda c: (not _fold(c["name"]).startswith(words[0]), c["name"]))

    names = deck_card_names(session)
    decks = []
    for summary in deck_summaries(session):
        by_name = _matches(summary.deck.name, words)
        hits = [name for name in names.get(summary.deck.id, []) if _matches(name, words)]
        if by_name or hits:
            decks.append({"id": summary.deck.id, "name": summary.deck.name, "image_url": summary.deck.commander_image or None,
                          "cards": list(dict.fromkeys(hits))[:3], "by_name": by_name})
    decks.sort(key=lambda d: (not d["by_name"], d["name"]))

    wishes = [
        {"name": row.card.name, "card_id": row.card.id, "image_url": row.card.image_url,
         "price": row.current_price, "target": row.entry.target_price}
        for row in wishlist_rows(session) if _matches(row.card.name, words)
    ]
    return {"query": query, "cards": found_cards[:PER_GROUP], "decks": decks[:PER_GROUP], "wishlist": wishes[:PER_GROUP],
            "more": {"cards": max(0, len(found_cards) - PER_GROUP), "decks": max(0, len(decks) - PER_GROUP)}}
