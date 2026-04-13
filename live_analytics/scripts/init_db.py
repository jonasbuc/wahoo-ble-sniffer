"""
Initialise (or re-initialise) the SQLite database for Live Analytics.

Usage:
    python init_db.py            # uses default path
    python init_db.py path/to.db # custom path
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow running as a standalone script
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from live_analytics.app.config import DB_PATH, ensure_dirs
from live_analytics.app.storage.sqlite_store import init_db


def main() -> None:
    db = Path(sys.argv[1]) if len(sys.argv) > 1 else DB_PATH
    ensure_dirs()
    init_db(db)
    print(f"Database initialised at {db}")


if __name__ == "__main__":
    main()
