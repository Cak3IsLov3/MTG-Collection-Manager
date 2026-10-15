"""Downloads: the collection and the wishlist as CSV."""

import csv
import io

from sqlmodel import Session

from app.services.collection import collection_rows
from app.services.wishlist import wishlist_rows

# Moxfield's collection export, which Moxfield, Archidekt and this app's CSV import all read.
MOXFIELD_HEADER = [
    "Count", "Tradelist Count", "Name", "Edition", "Condition", "Language", "Foil", "Tags", "Last Modified",
    "Collector Number", "Alter", "Proxy", "Purchase Price",
]


def collection_csv(session: Session) -> str:
    out = io.StringIO()
    writer = csv.writer(out, quoting=csv.QUOTE_ALL, lineterminator="\n")
    writer.writerow(MOXFIELD_HEADER)
    for row in collection_rows(session):
        writer.writerow([
            row.entry.quantity, 0, row.card.name, row.card.set_code, "Near Mint", "English",
            "foil" if row.entry.is_foil else "", "", row.entry.added_at.strftime("%Y-%m-%d %H:%M:%S"),
            row.card.collector_number or "", "False", "False", "",
        ])
    return out.getvalue()


def wishlist_csv(session: Session) -> str:
    out = io.StringIO()
    writer = csv.writer(out, quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
    writer.writerow(["Name", "Set", "Set name", "Collector Number", "Target price (EUR)", "Current price (EUR)", "Cardmarket"])
    for row in wishlist_rows(session):
        writer.writerow([
            row.card.name, row.card.set_code.upper(), row.card.set_name, row.card.collector_number or "",
            f"{row.entry.target_price:.2f}",
            "" if row.current_price is None else f"{row.current_price:.2f}",
            row.entry.cardmarket_url or "",
        ])
    return out.getvalue()
