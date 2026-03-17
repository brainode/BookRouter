# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 The ScanBookShelf Authors

import argparse
import sqlite3
from pathlib import Path


def get_regular_tables(conn: sqlite3.Connection) -> list[str]:
    cursor = conn.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
          AND name NOT LIKE 'sqlite_%'
          AND (sql IS NULL OR sql NOT LIKE 'CREATE VIRTUAL TABLE%')
        ORDER BY name
        """
    )
    return [row[0] for row in cursor.fetchall()]


def truncate_db(db_path: Path, vacuum: bool = False) -> None:
    if not db_path.exists():
        raise FileNotFoundError(f"DB file not found: {db_path}")

    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("PRAGMA foreign_keys = OFF;")
        tables = get_regular_tables(conn)

        for table in tables:
            conn.execute(f'DELETE FROM "{table}"')

        conn.execute("DELETE FROM sqlite_sequence")
        conn.commit()

        if vacuum:
            conn.execute("VACUUM")

        print(f"Done. Cleared tables: {', '.join(tables) if tables else '(none)'}")
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Truncate all data in SQLite DB, keep schema.")
    parser.add_argument("--db", default="books.db", help="Path to sqlite database file")
    parser.add_argument("--vacuum", action="store_true", help="Run VACUUM after truncate")
    args = parser.parse_args()

    db_path = Path(args.db).resolve()
    truncate_db(db_path, vacuum=args.vacuum)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
