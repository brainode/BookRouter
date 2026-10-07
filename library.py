# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 The ScanBookShelf Authors

"""CLI обслуживания библиотеки: журнал действий, откат, чистка записей."""

import argparse
import os
import sys

from config import OUTPUT_BOOKS_FOLDER
from db import BookDB
from library_ops import LibraryOpError, prune_missing, undo_action


def cmd_actions(db, args) -> int:
    rows = db.conn.execute("SELECT * FROM actions_log ORDER BY id DESC LIMIT ?", (args.limit,)).fetchall()
    for r in rows:
        undone = f"отменено {r['undone_at']}" if r["undone_at"] else ""
        print(f"{r['id']} | {r['created_at']} | {r['action']} | {r['summary']} | {undone}")
    return 0


def cmd_undo(db, args) -> int:
    undo_action(db, args.action_id, args.output)
    print(f"↩️ Действие #{args.action_id} отменено")
    return 0


def cmd_prune_missing(db, args) -> int:
    found = prune_missing(db, args.apply)
    for f in found:
        print(f"{f['id']} | {f['status']} | {f['original_path']}")
    print(f"{'Удалено' if args.apply else 'Будет удалено (используй --apply)'}: {len(found)}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Обслуживание библиотеки BookRouter")
    parser.add_argument("--db", default="books.db")
    parser.add_argument("--output", default=OUTPUT_BOOKS_FOLDER)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("actions")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=cmd_actions)
    p = sub.add_parser("undo")
    p.add_argument("action_id", type=int)
    p.set_defaults(func=cmd_undo)
    p = sub.add_parser("prune-missing")
    p.add_argument("--apply", action="store_true")
    p.set_defaults(func=cmd_prune_missing)
    args = parser.parse_args(argv)
    if not os.path.exists(args.db):
        parser.error("Database does not exist")
    db = BookDB(args.db)
    try:
        return args.func(db, args)
    except LibraryOpError as exc:
        print(f"⛔ {exc}")
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
