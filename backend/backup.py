"""Daily database backup and restore (SPMS-NF-003: RPO <= 24 h, RTO <= 2 h).

    python -m backend.backup              # make a backup now (schedule this daily)
    python -m backend.backup --list       # show backups
    python -m backend.backup --restore backups/spms-20261101-0200.db

Uses SQLite's online backup API, so it is safe while the server is running. Keeps the newest 7 backups.
Admins can also trigger and list backups through the API.
"""
import argparse
import os
import sqlite3
from contextlib import closing
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status

from backend import auth
from backend.auth import ADMIN, User, require_roles
from backend.db import DATABASE_URL, engine

KEEP = 7


def backup_dir() -> Path:
    return Path(os.getenv("SPMS_BACKUP_DIR", "backups"))


def database_file() -> Path:
    if not DATABASE_URL.startswith("sqlite:///"):
        raise RuntimeError("backend.backup handles SQLite; for PostgreSQL use pg_dump")
    return Path(DATABASE_URL.removeprefix("sqlite:///"))


def list_backups() -> list[Path]:
    folder = backup_dir()
    return sorted(folder.glob("spms-*.db")) if folder.is_dir() else []


def backup_now() -> Path:
    """Copy the live database to backups/spms-YYYYMMDD-HHMMSS.db and prune old copies."""
    folder = backup_dir()
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"spms-{auth.utcnow():%Y%m%d-%H%M%S}.db"
    n = 1
    while target.exists():
        target = folder / f"spms-{auth.utcnow():%Y%m%d-%H%M%S}-{n}.db"
        n += 1
    # closing(): `with sqlite3.connect()` alone does not close the file, and Windows can't delete open files
    with closing(sqlite3.connect(database_file())) as src, closing(sqlite3.connect(target)) as dst:
        src.backup(dst)
    for old in list_backups()[:-KEEP]:
        old.unlink()
    return target


def restore(backup_file: Path) -> None:
    """Replace the live database contents with a backup (RTO: seconds for a SQLite file)."""
    backup_file = Path(backup_file)
    if not backup_file.is_file():
        raise FileNotFoundError(backup_file)
    engine.dispose()  # close pooled connections before overwriting
    with closing(sqlite3.connect(backup_file)) as src, closing(sqlite3.connect(database_file())) as dst:
        src.backup(dst)


router = APIRouter(prefix="/api/v1/admin/backups", tags=["backup"])


@router.get("")
def get_backups(user: User = Depends(require_roles(ADMIN))):
    return [{"file": p.name, "bytes": p.stat().st_size} for p in list_backups()]


@router.post("", status_code=201)
def make_backup(user: User = Depends(require_roles(ADMIN))):
    try:
        path = backup_now()
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    return {"file": path.name, "bytes": path.stat().st_size}


def main() -> None:
    parser = argparse.ArgumentParser(description="SPMS database backup")
    parser.add_argument("--restore", metavar="FILE")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()
    if args.list:
        for p in list_backups():
            print(p)
    elif args.restore:
        restore(Path(args.restore))
        print(f"Restored {args.restore}")
    else:
        print(f"Backup written: {backup_now()}")


if __name__ == "__main__":
    main()
