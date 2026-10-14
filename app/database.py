from collections.abc import Iterator

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app import models  # noqa: F401  (registers tables on SQLModel.metadata)
from app.config import get_settings


def _make_engine(url: str):
    kwargs: dict = {}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if url in {"sqlite://", "sqlite:///:memory:"}:
            # One shared connection, otherwise every session sees an empty in-memory db.
            kwargs["poolclass"] = StaticPool
    return create_engine(url, **kwargs)


engine = _make_engine(get_settings().database_url)


def add_missing_columns(target=None) -> None:
    """create_all() never alters an existing table, so add columns introduced after a table was created."""
    target = target or engine
    if target.dialect.name != "sqlite":
        return
    added = {
        "card": {"colors": "VARCHAR"},
        "deck": {"bracket": "INTEGER", "archidekt_id": "INTEGER", "synced_at": "DATETIME"},
        "deckentry": {
            "unassigned": "BOOLEAN NOT NULL DEFAULT 0",
            "set_code": "VARCHAR",
            "collector_number": "VARCHAR",
            "scryfall_id": "VARCHAR",
            "foil": "BOOLEAN",
        },
        "pricesnapshot": {"eur_from_set": "VARCHAR", "eur_foil_from_set": "VARCHAR"},
    }
    with target.begin() as connection:
        for table, new_columns in added.items():
            columns = {row[1] for row in connection.exec_driver_sql(f"PRAGMA table_info({table})")}
            for column, definition in new_columns.items():
                if columns and column not in columns:
                    connection.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def init_db() -> None:
    SQLModel.metadata.create_all(engine)
    add_missing_columns()


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session
