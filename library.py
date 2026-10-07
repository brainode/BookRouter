# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 The ScanBookShelf Authors

"""CLI обслуживания библиотеки: журнал действий, откат, чистка записей."""

import argparse
import os
import sys

from config import OUTPUT_BOOKS_FOLDER
from authors import add_alias, create_author, find_author, suggest_merges
from db import DONE_STATUSES, BookDB
from library_ops import LibraryOpError, merge_authors, prune_missing, undo_action


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


def cmd_backfill_authors(db, args) -> int:
    marks = ",".join("?" * len(DONE_STATUSES))
    rows = db.conn.execute(
        f"SELECT id, author, category FROM books WHERE status IN ({marks}) AND author IS NOT NULL "
        "AND author != '' AND author_id IS NULL ORDER BY id", list(DONE_STATUSES)).fetchall()
    created = linked = 0
    for r in rows:
        parts = (r["category"] or "").split(" | ")
        slug_hint = parts[2] if (r["category"] or "").startswith("Художественные | ") and len(parts) >= 3 else None
        author = find_author(db, r["author"])
        if author is not None:
            add_alias(db, author["id"], r["author"], commit=False)
        elif slug_hint:
            row = db.conn.execute("SELECT id FROM authors WHERE slug = ?", (slug_hint,)).fetchone()
            if row:
                author = {"id": row["id"]}
                add_alias(db, row["id"], r["author"], commit=False)
            else:
                author = create_author(db, r["author"], slug=slug_hint, commit=False)
                created += 1
        else:
            before = db.conn.execute("SELECT COUNT(*) FROM authors").fetchone()[0]
            author = _resolve_nocommit(db, r["author"])
            if author is not None:
                created += db.conn.execute("SELECT COUNT(*) FROM authors").fetchone()[0] - before
        if author is None:
            continue
        db.conn.execute("UPDATE books SET author_id = ? WHERE id = ?", (author["id"], r["id"]))
        linked += 1
    if args.apply:
        db.conn.commit()
    else:
        db.conn.rollback()
    print(f"{'' if args.apply else '(пробный запуск, используй --apply) '}авторов создано {created}, книг привязано {linked}")
    return 0


def _resolve_nocommit(db, name):
    from normalization import is_unknown_label
    if not name or is_unknown_label(name):
        return None
    return create_author(db, name, commit=False)


def _author_line(a: dict) -> str:
    return f"{a['id']} | {a['name']} | {a['slug']} | {a['books']}"


def cmd_suggest_authors(db, args) -> int:
    for p in suggest_merges(db, args.limit):
        print(f"{_author_line(p['a'])} ↔ {_author_line(p['b'])} ({p['reason']})")
    return 0


def cmd_merge_authors(db, args) -> int:
    if not args.apply:
        from authors import get_author
        target = get_author(db, args.target)
        if target is None:
            raise LibraryOpError(f"Автор #{args.target} не найден")
        marks = ",".join("?" * len(args.sources))
        for b in db.conn.execute(f"SELECT id, title, category FROM books WHERE author_id IN ({marks})",
                                 list(args.sources)).fetchall():
            print(f"книга #{b['id']} {b['title']}: {b['category']} → автор {target['slug']}")
        print("Это план; для выполнения добавь --apply")
        return 0
    action_id = merge_authors(db, args.target, args.sources, args.output)
    print(f"✅ действие #{action_id}")
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
    p = sub.add_parser("backfill-authors")
    p.add_argument("--apply", action="store_true")
    p.set_defaults(func=cmd_backfill_authors)
    p = sub.add_parser("suggest-authors")
    p.add_argument("--limit", type=int, default=30)
    p.set_defaults(func=cmd_suggest_authors)
    p = sub.add_parser("merge-authors")
    p.add_argument("target", type=int)
    p.add_argument("sources", type=int, nargs="+")
    p.add_argument("--apply", action="store_true")
    p.set_defaults(func=cmd_merge_authors)
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
