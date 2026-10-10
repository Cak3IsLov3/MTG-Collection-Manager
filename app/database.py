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
    with target.begin() as connection:
        columns = {row[1] for row in connection.exec_driver_sql("PRAGMA table_info(deck)")}
        if columns and "bracket" not in columns:
            connection.exec_driver_sql("ALTER TABLE deck ADD COLUMN bracket INTEGER")


def init_db() -> None:
    SQLModel.metadata.create_all(engine)
    add_missing_columns()


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session
