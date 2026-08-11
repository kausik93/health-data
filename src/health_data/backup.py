"""Weekly local backup of health.db, run before either sync script touches it.

Uses sqlite3's own backup API rather than a raw file copy so a backup is never
taken mid-write. Dated snapshots accumulate in backups/; anything past
RETENTION gets pruned, oldest first.
"""

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from health_data.db import DEFAULT_DB_PATH

DEFAULT_BACKUP_DIR = Path(
    os.environ.get(
        "HEALTH_DB_BACKUP_DIR",
        str(Path(__file__).resolve().parents[2] / "backups"),
    )
)
RETENTION = int(os.environ.get("HEALTH_DB_BACKUP_RETENTION", "12"))  # ~3 months weekly


def backup(db_path: Path, backup_dir: Path, retention: int = RETENTION) -> Path | None:
    if not db_path.exists():
        return None  # nothing ingested yet - nothing to back up

    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest_path = backup_dir / f"health_{stamp}.db"

    src = sqlite3.connect(db_path)
    dest = sqlite3.connect(dest_path)
    try:
        src.backup(dest)
    finally:
        dest.close()
        src.close()

    prune(backup_dir, retention)
    return dest_path


def prune(backup_dir: Path, retention: int) -> None:
    backups = sorted(backup_dir.glob("health_*.db"))
    for stale in backups[:-retention] if retention > 0 else backups:
        stale.unlink()


def main() -> None:
    dest = backup(DEFAULT_DB_PATH, DEFAULT_BACKUP_DIR)
    if dest is None:
        print("backup-db: no health.db yet, skipping")
    else:
        remaining = len(list(DEFAULT_BACKUP_DIR.glob("health_*.db")))
        print(f"backup-db: wrote {dest.name} ({remaining} backups retained)")


if __name__ == "__main__":
    main()
