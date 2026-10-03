# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 The ScanBookShelf Authors

import argparse
from pathlib import Path

from db import BookDB


def main(argv=None):
    parser = argparse.ArgumentParser(description="Восстановить CSV результатов из SQLite")
    parser.add_argument("--db", default="books.db")
    parser.add_argument("--csv", default="results.csv")
    args = parser.parse_args(argv)
    if not Path(args.db).is_file():
        parser.error("Database does not exist")
    db = BookDB(args.db)
    try:
        db.export_results(args.csv)
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
