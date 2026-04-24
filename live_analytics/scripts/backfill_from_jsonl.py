"""
Backfill the SQLite database from existing JSONL session files.

Useful when:
  - The database was deleted and needs to be rebuilt from raw data
  - After a fresh clone where only JSONL files were restored from backup
  - After switching DB paths

Usage:
    python live_analytics/scripts/backfill_from_jsonl.py
    python live_analytics/scripts/backfill_from_jsonl.py --dry-run
    python live_analytics/scripts/backfill_from_jsonl.py --db path/to.db --sessions path/to/sessions/

The script is idempotent: sessions already present in the DB are skipped.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

# Allow running as a standalone script from any CWD
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from live_analytics.app.config import DB_PATH, SESSIONS_DIR, ensure_dirs
from live_analytics.app.storage.sqlite_store import (
    end_session,
    init_db,
    list_sessions,
    upsert_session,
    increment_record_count,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
)
logger = logging.getLogger("backfill")


def _first_record(jsonl_path: Path) -> dict | None:
    """Return the first valid JSON record from a JSONL file, or None."""
    try:
        with open(jsonl_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        return json.loads(line)
                    except json.JSONDecodeError:
                        continue
    except OSError:
        pass
    return None


def _last_record(jsonl_path: Path) -> dict | None:
    """Return the last valid JSON record from a JSONL file, or None."""
    last = None
    try:
        with open(jsonl_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        last = json.loads(line)
                    except json.JSONDecodeError:
                        continue
    except OSError:
        pass
    return last


def _count_records(jsonl_path: Path) -> int:
    """Count valid JSON lines in a JSONL file."""
    count = 0
    try:
        with open(jsonl_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        json.loads(line)
                        count += 1
                    except json.JSONDecodeError:
                        pass
    except OSError:
        pass
    return count


def backfill(db_path: Path, sessions_dir: Path, dry_run: bool = False) -> int:
    """
    Scan *sessions_dir* for JSONL files and insert missing sessions into *db_path*.

    Returns the number of sessions inserted.
    """
    if not sessions_dir.exists():
        logger.warning("Sessions directory does not exist: %s", sessions_dir)
        return 0

    if not dry_run:
        ensure_dirs()
        init_db(db_path)

    # Build set of already-known session IDs
    existing: set[str] = set()
    if not dry_run:
        for s in list_sessions(db_path):
            existing.add(s.session_id)
    logger.info("Existing sessions in DB: %d", len(existing))

    session_dirs = sorted(
        (d for d in sessions_dir.iterdir() if d.is_dir()),
        key=lambda d: d.name,
    )
    logger.info("Session directories found: %d", len(session_dirs))

    inserted = 0
    skipped = 0

    for session_dir in session_dirs:
        session_id = session_dir.name
        jsonl_path = session_dir / "telemetry.jsonl"

        if not jsonl_path.exists():
            logger.warning("  skip %s – no telemetry.jsonl", session_id)
            continue

        if session_id in existing:
            logger.debug("  skip %s – already in DB", session_id)
            skipped += 1
            continue

        first = _first_record(jsonl_path)
        if first is None:
            logger.warning("  skip %s – empty or unreadable JSONL", session_id)
            continue

        start_unix_ms: int = first.get("unix_ms", 0)
        scenario_id: str = first.get("scenario_id", "")
        record_count = _count_records(jsonl_path)

        last = _last_record(jsonl_path)
        end_unix_ms: int | None = last.get("unix_ms") if last else None

        if dry_run:
            logger.info(
                "  [dry-run] would insert %s  start=%d  records=%d  scenario=%r",
                session_id, start_unix_ms, record_count, scenario_id,
            )
        else:
            upsert_session(db_path, session_id, start_unix_ms, scenario_id)
            if record_count > 0:
                increment_record_count(db_path, session_id, record_count)
            if end_unix_ms is not None:
                end_session(db_path, session_id, end_unix_ms)
            logger.info(
                "  inserted  %s  start=%d  records=%d  scenario=%r",
                session_id, start_unix_ms, record_count, scenario_id,
            )

        inserted += 1

    logger.info(
        "Backfill complete. Inserted=%d  Skipped=%d  Total scanned=%d",
        inserted, skipped, len(session_dirs),
    )
    return inserted


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill SQLite DB from JSONL session files",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--db", type=Path, default=DB_PATH, help="Path to SQLite DB")
    parser.add_argument("--sessions", type=Path, default=SESSIONS_DIR, help="Path to sessions dir")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be inserted without writing")
    args = parser.parse_args()

    n = backfill(args.db, args.sessions, dry_run=args.dry_run)
    if args.dry_run:
        print(f"\n[dry-run] {n} session(s) would be inserted.")
    else:
        print(f"\nDone. {n} session(s) inserted.")


if __name__ == "__main__":
    main()
