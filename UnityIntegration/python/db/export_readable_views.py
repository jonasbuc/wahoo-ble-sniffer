#!/usr/bin/env python3
"""Export readable views to CSV and (optionally) Parquet.

Writes one CSV per readable view into the given output directory. If
`pyarrow` is available, also writes a Parquet file alongside the CSV.

Usage:
  . .venv/bin/activate
  python UnityIntegration/python/db/export_readable_views.py --out exports/
"""
from __future__ import annotations
import sqlite3
import csv
from pathlib import Path
from typing import Iterable, Tuple, List, Any

DB = Path("collector_out/vrs.sqlite")

DEFAULT_VIEWS = [
    "headpose_readable",
    "bike_readable",
    "hr_readable",
    "events_readable",
    "sessions_readable",
]


def rows_and_cols(cur: sqlite3.Cursor, name: str) -> Tuple[List[str], List[Tuple[Any, ...]]]:
    q = f"SELECT * FROM \"{name}\";"
    cur.execute(q)
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
    return cols, rows


def write_csv(path: Path, cols: List[str], rows: Iterable[Tuple[Any, ...]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(cols)
        for r in rows:
            writer.writerow(["" if v is None else v for v in r])


def try_write_parquet(path: Path, cols: List[str], rows: List[Tuple[Any, ...]]) -> bool:
    """Try to write *rows* as a Parquet file.  Returns False if pyarrow is unavailable.

    The conversion from a list-of-tuples (SQLite row format) to pyarrow's
    columnar Table is done via a dict-of-lists transpose:
      {col_name: [row[i] for each row]} for each column index i.
    This avoids creating an intermediate pandas DataFrame and works with
    pyarrow's native ``pa.table(dict)`` constructor.
    """
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except Exception:
        return False
    # Transpose list-of-rows → dict-of-columns (pyarrow's native input format).
    data = {c: [r[i] for r in rows] for i, c in enumerate(cols)}
    table = pa.table(data)
    pq.write_table(table, str(path))
    return True


def export_all_views(out_dir: str | Path, db_path: str | Path = DB, views: Iterable[str] = DEFAULT_VIEWS) -> List[Path]:
    out = Path(out_dir)
    db = Path(db_path)
    if not db.exists():
        raise FileNotFoundError(f"DB not found: {db}")
    conn = sqlite3.connect(str(db))
    cur = conn.cursor()
    written: List[Path] = []
    for v in views:
        try:
            cols, rows = rows_and_cols(cur, v)
        except sqlite3.OperationalError:
            # view/table doesn't exist; skip
            continue
        csv_path = out / f"{v}.csv"
        write_csv(csv_path, cols, rows)
        written.append(csv_path)
        # try parquet
        pq_path = out / f"{v}.parquet"
        if rows and try_write_parquet(pq_path, cols, rows):
            written.append(pq_path)
    conn.close()
    return written


def main() -> None:
    import argparse

    p = argparse.ArgumentParser(description="Export readable views to CSV/Parquet")
    p.add_argument("--out", default="exports", help="Output directory")
    p.add_argument("--db", default=str(DB), help="Path to sqlite DB")
    p.add_argument("--views", nargs="*", help="Optional list of views to export")
    args = p.parse_args()
    views = args.views if args.views else DEFAULT_VIEWS
    written = export_all_views(args.out, args.db, views)
    print("Written:")
    for w in written:
        print(" -", w)


if __name__ == "__main__":
    main()
