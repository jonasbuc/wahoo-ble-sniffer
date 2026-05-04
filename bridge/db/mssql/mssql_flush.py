#!/usr/bin/env python3
"""
mssql_flush.py
==============
Read JSONL session log-files produced by ``collector_tail.py`` and
bulk-insert them into a Microsoft SQL Server database.

Architecture
------------
During a VR session the collector appends one JSON line per chunk to a
local ``.jsonl`` file — this is extremely fast and never blocks the
real-time capture loop.  When the session ends (or on explicit request)
this module reads the log file, groups the records by stream/table, and
bulk-inserts them into MSSQL using ``pyodbc`` with ``fast_executemany``.

The JSONL file acts as a durable buffer:

  .jsonl  →  flush_session()  →  MSSQL
  .jsonl  →  .jsonl.done          (renamed after successful flush)
  .jsonl  →  .jsonl.failed.{N}   (renamed on permanent failure, kept for retry)

Log-file format
---------------
Each line is a self-contained JSON object::

    {"stream": 1, "ts_ns": 171…, "sid": 42, "data": {…}}

``stream`` selects the target table (1=headpose, 2=bike, 3=hr, 4=events).
``ts_ns`` is the collector receipt timestamp (nanoseconds since epoch).
``sid`` is the session_id.
``data`` contains the column values.

Usage
-----
As a library::

    from bridge.db.mssql.mssql_flush import flush_session
    flush_session("/path/to/session_42.jsonl", conn_str)

CLI::

    python -m bridge.db.mssql_flush \\
        --logdir collector_out/logs \\
        --conn "DRIVER={ODBC Driver 18 for SQL Server};SERVER=…;DATABASE=…;UID=…;PWD=…"
"""
from __future__ import annotations

import argparse
import glob
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

LOG = logging.getLogger("mssql_flush")

# ── pyodbc import (optional at module level) ─────────────────────────────────

try:
    import pyodbc
    HAVE_PYODBC = True
except ImportError:
    pyodbc = None  # type: ignore[assignment]
    HAVE_PYODBC = False


# ── Constants ─────────────────────────────────────────────────────────────────

# INSERT statements keyed by stream id.
_INSERT_SQL: Dict[int, str] = {
    1: (
        "INSERT INTO headpose"
        " (session_id, recv_ts_ns, seq, unity_t, px, py, pz, qx, qy, qz, qw)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    ),
    2: (
        "INSERT INTO bike"
        " (session_id, recv_ts_ns, seq, unity_t, speed, steering, brake_front, brake_rear)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
    ),
    3: (
        "INSERT INTO hr"
        " (session_id, recv_ts_ns, seq, unity_t, hr_bpm)"
        " VALUES (?, ?, ?, ?, ?)"
    ),
    4: (
        "INSERT INTO events"
        " (session_id, recv_ts_ns, seq, unity_t, json_payload)"
        " VALUES (?, ?, ?, ?, ?)"
    ),
}


# ── Row builders ──────────────────────────────────────────────────────────────

def _build_row(stream: int, sid: int, ts_ns: int, data: Dict[str, Any]) -> Optional[tuple]:
    """Convert a parsed JSONL *data* dict to a parameter tuple for INSERT.

    Returns None if required keys are missing (the line is skipped with a
    warning rather than aborting the entire flush).
    """
    try:
        if stream == 1:
            return (
                sid, ts_ns,
                data["seq"], data["ut"],
                data["px"], data["py"], data["pz"],
                data["qx"], data["qy"], data["qz"], data["qw"],
            )
        elif stream == 2:
            return (
                sid, ts_ns,
                data["seq"], data["ut"],
                data["speed"], data["steering"],
                data["bf"], data["br"],
            )
        elif stream == 3:
            return (
                sid, ts_ns,
                data["seq"], data["ut"],
                data["hr_bpm"],
            )
        elif stream == 4:
            return (
                sid, ts_ns,
                data["seq"], data["ut"],
                data["json"],
            )
    except KeyError as exc:
        LOG.warning("Missing key %s in stream %d record: %s", exc, stream, data)
    return None


# ── JSONL parsing ─────────────────────────────────────────────────────────────

def parse_jsonl(path: str | Path) -> Dict[int, List[tuple]]:
    """Parse a ``.jsonl`` session log and return rows grouped by stream.

    Returns
    -------
    dict mapping stream_id (1-4) → list of parameter tuples ready for
    ``cursor.executemany``.  Corrupt or incomplete lines are skipped with
    a warning.
    """
    rows: Dict[int, List[tuple]] = {1: [], 2: [], 3: [], 4: []}
    skipped = 0

    with open(path, "r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                LOG.warning("Skipping corrupt JSON at %s:%d", path, lineno)
                skipped += 1
                continue

            stream = obj.get("stream")
            sid = obj.get("sid")
            ts_ns = obj.get("ts_ns")
            data = obj.get("data")

            if stream not in (1, 2, 3, 4) or sid is None or ts_ns is None or data is None:
                LOG.warning("Skipping incomplete record at %s:%d", path, lineno)
                skipped += 1
                continue

            row = _build_row(stream, sid, ts_ns, data)
            if row is not None:
                rows[stream].append(row)
            else:
                skipped += 1

    total = sum(len(v) for v in rows.values())
    if skipped:
        LOG.warning("Parsed %s: %d rows, %d skipped", path, total, skipped)
    else:
        LOG.info("Parsed %s: %d rows", path, total)
    return rows


# ── Session upsert ────────────────────────────────────────────────────────────

def _ensure_session(cursor, sid: int, started_ms: int, session_dir: str | None) -> None:
    """Insert the session row if it doesn't already exist (MERGE / IF NOT EXISTS).

    ``started_ms`` must be a Unix-epoch millisecond timestamp (e.g. from
    ``int(time.time() * 1000)``).  A value of 0 is stored as-is and will
    produce the date 1970-01-01 in the MSSQL readable view — this is a
    sign that the caller passed a default/missing value.
    """
    if started_ms == 0:
        LOG.warning(
            "_ensure_session: started_ms=0 for session %s — "
            "the sessions table will show 1970-01-01. "
            "Pass the real session-start Unix-ms when calling flush_session().",
            sid,
        )
    cursor.execute(
        "IF NOT EXISTS (SELECT 1 FROM sessions WHERE session_id = ?)"
        " INSERT INTO sessions (session_id, started_unix_ms, session_dir)"
        " VALUES (?, ?, ?)",
        (sid, sid, started_ms, session_dir),
    )


# ── Bulk insert ───────────────────────────────────────────────────────────────

def _bulk_insert(
    cursor,
    stream: int,
    rows: List[tuple],
    batch_size: int = 5000,
) -> int:
    """Insert *rows* into the table for *stream* in batches.

    Uses ``fast_executemany=True`` (pyodbc optimisation that packs multiple
    rows into a single TDS packet) for dramatically better throughput compared
    to row-by-row inserts.

    Returns the number of rows inserted.
    """
    sql = _INSERT_SQL.get(stream)
    if sql is None or not rows:
        return 0

    inserted = 0
    for i in range(0, len(rows), batch_size):
        batch = rows[i : i + batch_size]
        cursor.executemany(sql, batch)
        inserted += len(batch)
    return inserted


# ── Public API ────────────────────────────────────────────────────────────────

def flush_session(
    logfile: str | Path,
    conn_str: str,
    *,
    session_id: Optional[int] = None,
    started_ms: Optional[int] = None,
    session_dir: Optional[str] = None,
    batch_size: int = 5000,
    rename_done: bool = True,
) -> Dict[str, int]:
    """Read a JSONL session log and bulk-insert all rows into MSSQL.

    Parameters
    ----------
    logfile      : path to the ``.jsonl`` file
    conn_str     : pyodbc connection string
    session_id   : override session id (default: taken from first record)
    started_ms   : session start timestamp in ms (for the sessions table)
    session_dir  : directory name for the sessions table
    batch_size   : rows per ``executemany`` call (default 5000)
    rename_done  : if True, rename ``file.jsonl`` → ``file.jsonl.done``
                   after successful flush

    Returns
    -------
    dict with keys ``headpose``, ``bike``, ``hr``, ``events`` → row counts

    Raises
    ------
    ImportError     if pyodbc is not installed
    FileNotFoundError if *logfile* does not exist
    pyodbc.Error    on database errors (caller should handle / retry)
    """
    if not HAVE_PYODBC:
        raise ImportError(
            "pyodbc is required for MSSQL flush.  Install: pip install pyodbc"
        )

    logfile = Path(logfile)
    if not logfile.exists():
        raise FileNotFoundError(f"JSONL log not found: {logfile}")

    rows = parse_jsonl(logfile)

    # Determine session_id from data if not overridden.
    if session_id is None:
        for stream_rows in rows.values():
            if stream_rows:
                session_id = stream_rows[0][0]  # first element of tuple is sid
                break
    if session_id is None:
        LOG.warning("No data in %s — nothing to flush", logfile)
        return {"headpose": 0, "bike": 0, "hr": 0, "events": 0}

    # ── Connect and insert ────────────────────────────────────────────
    conn = pyodbc.connect(conn_str, autocommit=False)
    cursor = conn.cursor()
    cursor.fast_executemany = True

    try:
        # Ensure the session row exists.
        _ensure_session(
            cursor,
            session_id,
            started_ms or 0,
            session_dir,
        )

        counts = {}
        stream_names = {1: "headpose", 2: "bike", 3: "hr", 4: "events"}
        for stream_id, name in stream_names.items():
            n = _bulk_insert(cursor, stream_id, rows[stream_id], batch_size)
            counts[name] = n

        conn.commit()
        LOG.info(
            "Flushed session %s to MSSQL: headpose=%d bike=%d hr=%d events=%d",
            session_id, counts["headpose"], counts["bike"],
            counts["hr"], counts["events"],
        )
    except Exception:
        conn.rollback()
        LOG.exception("MSSQL flush failed for %s — rolled back", logfile)
        raise
    finally:
        cursor.close()
        conn.close()

    # ── Mark as done ──────────────────────────────────────────────────
    if rename_done:
        done_path = logfile.with_suffix(logfile.suffix + ".done")
        try:
            logfile.rename(done_path)
            LOG.info("Renamed %s → %s", logfile.name, done_path.name)
        except OSError as exc:
            LOG.warning("Could not rename %s: %s", logfile, exc)

    return counts


def flush_all(
    logdir: str | Path,
    conn_str: str,
    *,
    batch_size: int = 5000,
) -> int:
    """Flush all pending ``.jsonl`` files in *logdir* to MSSQL.

    Skips files that have already been flushed (``.jsonl.done``).

    Returns the total number of files flushed successfully.
    """
    logdir = Path(logdir)
    pattern = str(logdir / "session_*.jsonl")
    files = sorted(glob.glob(pattern))
    flushed = 0

    for f in files:
        # Skip already-done files (shouldn't match the glob, but be safe).
        if f.endswith(".done") or f.endswith(".failed"):
            continue
        try:
            flush_session(f, conn_str, batch_size=batch_size)
            flushed += 1
        except Exception:
            # Rename to .failed so we don't retry infinitely.
            failed = f + f".failed.{int(time.time())}"
            try:
                os.rename(f, failed)
            except OSError:
                pass
            LOG.exception("Failed to flush %s — renamed to %s", f, failed)

    return flushed


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser(
        description="Flush JSONL session logs to MSSQL.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Example connection string:\n"
            '  "DRIVER={ODBC Driver 18 for SQL Server};'
            "SERVER=localhost;DATABASE=vrs_cycling;"
            'UID=sa;PWD=YourPassword;TrustServerCertificate=yes"'
        ),
    )
    p.add_argument(
        "--logdir",
        required=True,
        help="Directory containing session_*.jsonl files",
    )
    p.add_argument(
        "--conn",
        required=True,
        help="pyodbc connection string for MSSQL",
    )
    p.add_argument(
        "--batch-size",
        type=int,
        default=5000,
        help="Rows per executemany batch (default: 5000)",
    )
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    n = flush_all(args.logdir, args.conn, batch_size=args.batch_size)
    LOG.info("Done — flushed %d session log(s)", n)


if __name__ == "__main__":
    main()
