"""Parse collection CSV exports from Archidekt and Moxfield (or anything with quantity + name)."""

import csv
import io
from dataclasses import dataclass, field

from app.scryfall import CardRef

QUANTITY_COLUMNS = ("quantity", "count", "qty", "amount")
NAME_COLUMNS = ("name", "card name", "card")
SET_COLUMNS = ("edition code", "set code", "edition", "set")
NUMBER_COLUMNS = ("collector number", "collector_number")
SCRYFALL_ID_COLUMNS = ("scryfall id", "scryfall_id")
FOIL_COLUMNS = ("foil", "finish")
FOIL_VALUES = {"foil", "etched", "true", "yes", "1"}


class CsvFormatError(ValueError):
    pass


@dataclass
class ImportRow(CardRef):
    quantity: int = 1
    is_foil: bool = False


@dataclass
class ParsedCsv:
    format: str
    rows: list[ImportRow] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def detect_format(headers: list[str]) -> str:
    columns = {h.strip().lower() for h in headers if h}
    if "name" in columns:
        if "count" in columns and "edition" in columns:
            return "moxfield"
        if "quantity" in columns and columns & {"edition code", "edition name", "finish", "scryfall id"}:
            return "archidekt"
    if columns & set(NAME_COLUMNS) and columns & set(QUANTITY_COLUMNS):
        return "generic"
    raise CsvFormatError(
        "Unrecognised CSV: expected an Archidekt or Moxfield export, "
        "or at least a quantity and a name column."
    )


def _pick(row: dict[str, str], candidates: tuple[str, ...]) -> str:
    for column in candidates:
        value = row.get(column)
        if value and value.strip():
            return value.strip()
    return ""


def parse_collection_csv(content: bytes | str) -> ParsedCsv:
    text = content.decode("utf-8-sig", errors="replace") if isinstance(content, bytes) else content
    text = text.lstrip("﻿")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise CsvFormatError("The CSV file is empty.")
    parsed = ParsedCsv(format=detect_format(list(reader.fieldnames)))

    for raw in reader:
        row = {(k or "").strip().lower(): (v or "") for k, v in raw.items() if isinstance(v, str)}
        name = _pick(row, NAME_COLUMNS)
        if not name:
            continue
        try:
            quantity = int(_pick(row, QUANTITY_COLUMNS) or "1")
            if quantity < 1:
                raise ValueError
        except ValueError:
            parsed.errors.append(f"Line {reader.line_num}: invalid quantity for '{name}'")
            continue
        parsed.rows.append(
            ImportRow(
                name=name,
                set_code=_pick(row, SET_COLUMNS).lower() or None,
                collector_number=_pick(row, NUMBER_COLUMNS) or None,
                scryfall_id=_pick(row, SCRYFALL_ID_COLUMNS) or None,
                quantity=quantity,
                is_foil=_pick(row, FOIL_COLUMNS).lower() in FOIL_VALUES,
            )
        )
    return parsed
