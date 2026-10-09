"""Backups of the hosted app's data, and restoring one.

A backup is one zip: the database (copied with SQLite's own backup API, so it is consistent even
while the app writes) and each project's tests, drafts, saved paths, approved looks and secrets.
Run folders (screenshots, traces, reports) are left out unless asked for: they are large, and old
ones are pruned anyway. Secrets stay encrypted inside it; the key that opens them is not in it
(vault.py), so keep a copy of the key somewhere else, or a restored backup can't run its tests.

    nightshift hosted backup --data hosted-data --to backups [--keep 14] [--with-runs]
    nightshift hosted restore backups/nightshift-backup-20261010-031500.zip --data hosted-data

The server makes one every night by itself (`hosted serve --backup-dir`, `--backup-at`).
"""

from __future__ import annotations

import sqlite3
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

DB_NAME = "nightshift.db"
PREFIX = "nightshift-backup-"
# Inside each project folder; runs/ only with --with-runs.
PROJECT_PARTS = ("specs", "drafts", "recordings", "visual", "secrets.env")


class BackupError(RuntimeError):
    pass


def make_backup(data: Path, to: Path, keep: int = 14, with_runs: bool = False) -> Path:
    """Write a new backup zip into `to`, delete all but the newest `keep`, and return its path."""
    db = data / DB_NAME
    if not db.exists():
        raise BackupError(f"no database at {db}")
    to.mkdir(parents=True, exist_ok=True)
    target = to / f"{PREFIX}{datetime.now().strftime('%Y%m%d-%H%M%S')}.zip"
    partial = target.with_suffix(".partial")
    with tempfile.TemporaryDirectory() as scratch:
        copy = Path(scratch) / DB_NAME
        source, dest = sqlite3.connect(str(db)), sqlite3.connect(str(copy))
        try:
            source.backup(dest)
        finally:
            dest.close()
            source.close()
        with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.write(copy, DB_NAME)
            for project in sorted((data / "projects").glob("*")) if (data / "projects").exists() else []:
                parts = PROJECT_PARTS + (("runs",) if with_runs else ())
                for part in parts:
                    path = project / part
                    files = [path] if path.is_file() else sorted(p for p in path.rglob("*") if p.is_file())
                    for file in files:
                        archive.write(file, file.relative_to(data).as_posix())
    partial.replace(target)  # a half-written zip never looks like a backup
    for old in sorted(to.glob(f"{PREFIX}*.zip"))[:-keep] if keep > 0 else []:
        old.unlink(missing_ok=True)
    return target


def restore_backup(backup: Path, data: Path, force: bool = False) -> int:
    """Unpack a backup into `data`. Refuses to write over an existing database unless `force`.
    Returns the number of files restored."""
    if (data / DB_NAME).exists() and not force:
        raise BackupError(f"{data / DB_NAME} exists; stop the app and pass --force to replace it")
    root = data.resolve()
    with zipfile.ZipFile(backup) as archive:
        names = archive.namelist()
        if DB_NAME not in names:
            raise BackupError(f"{backup} is not a Nightshift backup (no {DB_NAME} in it)")
        for name in names:
            # A crafted zip can hold "../../somewhere": every file must land inside the data folder.
            target = (root / name).resolve()
            if root not in target.parents:
                raise BackupError(f"refusing a path that leaves the data folder: {name}")
        data.mkdir(parents=True, exist_ok=True)
        for leftover in (data / f"{DB_NAME}-wal", data / f"{DB_NAME}-shm"):
            leftover.unlink(missing_ok=True)  # they belong to the database being replaced
        archive.extractall(root)
    return len(names)


def newest(to: Path) -> str | None:
    """When the newest backup in `to` was made, as an ISO time; None if there is none."""
    found = sorted(to.glob(f"{PREFIX}*.zip")) if to.exists() else []
    if not found:
        return None
    stamp = found[-1].stem.removeprefix(PREFIX)
    try:
        return datetime.strptime(stamp, "%Y%m%d-%H%M%S").isoformat(timespec="seconds")
    except ValueError:
        return None
