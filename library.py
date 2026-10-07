# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 The ScanBookShelf Authors

"""CLI обслуживания библиотеки: журнал действий, откат, чистка записей."""

import argparse
import os
import sys

from config import OUTPUT_BOOKS_FOLDER
from authors import add_alias, create_author, find_author, suggest_merges
from db import DONE_STATUSES, BookDB
from library_ops import LibraryOpError, _genre_move_steps, merge_authors, prune_missing, set_author_genre, undo_action


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


def cmd_backfill_genres(db, args) -> int:
    from authors import fiction_genre
    rows = db.conn.execute("SELECT id, category FROM books WHERE book_genre IS NULL "
                           "AND category LIKE 'Художественные | %'").fetchall()
    if args.apply:
        for r in rows:
            db.conn.execute("UPDATE books SET book_genre = ? WHERE id = ?", (fiction_genre(r["category"]), r["id"]))
        db.conn.commit()
    print(f"{'Обновлено' if args.apply else 'Будет обновлено (используй --apply)'}: {len(rows)}")
    return 0


def cmd_rebuild_fiction(db, args) -> int:
    from authors import FICTION_PREFIX, get_author, majority_genre
    from llm import ALLOWED_CATEGORIES
    if db.conn.execute("SELECT 1 FROM books WHERE category LIKE 'Художественные | %' AND author_id IS NULL").fetchone():
        print("⛔ У части художественных книг нет автора: сначала backfill-authors")
        return 1
    order = [c[len(FICTION_PREFIX):] for c in ALLOWED_CATEGORIES if c.startswith(FICTION_PREFIX)]
    if args.author:
        ids = [args.author]
    else:
        ids = [r[0] for r in db.conn.execute("SELECT DISTINCT author_id FROM books WHERE author_id IS NOT NULL "
                                             "AND category LIKE 'Художественные | %' ORDER BY author_id")]
    authors_n = moved_n = 0
    first = last = None
    for aid in ids:
        author = get_author(db, aid)
        if author is None:
            raise LibraryOpError(f"Автор #{aid} не найден")
        genres = [r[0] for r in db.conn.execute("SELECT book_genre FROM books WHERE author_id = ? "
                                                "AND category LIKE 'Художественные | %'", (aid,)) if r[0]]
        genre = majority_genre(genres, order)
        if not genre:
            continue
        moves = len(_genre_move_steps(db, aid, genre, args.output))
        print(f"{author['name']} | было жанров {len(set(genres))} | {genre} | переносится {moves}")
        if args.apply:
            action_id = set_author_genre(db, aid, genre, args.output)
            if action_id is not None:
                authors_n += 1
                moved_n += moves
                first = action_id if first is None else first
                last = action_id
        else:
            authors_n += bool(moves)
            moved_n += moves
    tail = f", действия #{first}–#{last}" if first is not None else ""
    print(f"авторов {authors_n}, книг перенесено {moved_n}{tail}" + ("" if args.apply else " (план; добавь --apply)"))
    return 0


def cmd_set_author_genre(db, args) -> int:
    if not args.apply:
        from authors import get_author
        author = get_author(db, args.author_id)
        if author is None:
            raise LibraryOpError(f"Автор #{args.author_id} не найден")
        print(f"{author['name']}: переносится книг {len(_genre_move_steps(db, args.author_id, args.genre, args.output))}"
              " (план; добавь --apply)")
        return 0
    action_id = set_author_genre(db, args.author_id, args.genre, args.output)
    print("Без изменений" if action_id is None else f"✅ действие #{action_id}")
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
    p = sub.add_parser("backfill-genres")
    p.add_argument("--apply", action="store_true")
    p.set_defaults(func=cmd_backfill_genres)
    p = sub.add_parser("rebuild-fiction")
    p.add_argument("--apply", action="store_true")
    p.add_argument("--author", type=int)
    p.set_defaults(func=cmd_rebuild_fiction)
    p = sub.add_parser("set-author-genre")
    p.add_argument("author_id", type=int)
    p.add_argument("genre")
    p.add_argument("--apply", action="store_true")
    p.set_defaults(func=cmd_set_author_genre)
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
