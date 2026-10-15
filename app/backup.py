"""Daily copies of the SQLite database, of which the newest few are kept."""

import datetime as dt
import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from app.config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class Backup:
    path: Path
    size: int  # bytes
    made: dt.datetime

    @property
    def name(self) -> str:
        return self.path.name


def database_path(database_url: str) -> Path | None:
    """The file of a SQLite database URL ("sqlite:///./mtg.db"); None for anything else (in memory, other databases)."""
    if not database_url.startswith("sqlite:///"):
        return None
    path = database_url.removeprefix("sqlite:///")
    if path in {"", ":memory:"}:
        return None
    return Path(path)


def backup_folder() -> Path:
    return Path(get_settings().backup_dir)


def list_backups(folder: Path | None = None, stem: str | None = None) -> list[Backup]:
    """The backups in the folder, newest first."""
    folder = folder or backup_folder()
    source = database_path(get_settings().database_url)
    stem = stem or (source.stem if source else "mtg")
    if not folder.is_dir():
        return []
    backups = [
        Backup(path, path.stat().st_size, dt.datetime.fromtimestamp(path.stat().st_mtime))
        for path in folder.glob(f"{stem}-*.db")
    ]
    return sorted(backups, key=lambda backup: backup.path.name, reverse=True)


def make_backup(
    database_url: str | None = None, folder: Path | None = None, keep: int | None = None, today: dt.date | None = None
) -> Path | None:
    """Copy the database to `folder` as "<name>-<date>.db" (today's copy is replaced) and remove all
    but the newest `keep`. SQLite's own backup is used, so a copy made while the app writes is still
    whole. Returns the new file, or None when there is nothing to back up."""
    settings = get_settings()
    source = database_path(database_url or settings.database_url)
    keep = settings.backup_keep if keep is None else keep
    if source is None or keep <= 0 or not source.exists():
        return None
    folder = folder or backup_folder()
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{source.stem}-{(today or dt.date.today()).isoformat()}.db"
    partial = target.with_suffix(".db.part")
    original, copy = sqlite3.connect(source), sqlite3.connect(partial)
    try:
        original.backup(copy)
    finally:
        copy.close()
        original.close()
    partial.replace(target)
    for old in list_backups(folder, source.stem)[keep:]:
        old.path.unlink()
    logger.info("Database backed up to %s", target)
    return target


def has_backup_today(today: dt.date | None = None) -> bool:
    source = database_path(get_settings().database_url)
    stem = source.stem if source else "mtg"
    return (backup_folder() / f"{stem}-{(today or dt.date.today()).isoformat()}.db").exists()
