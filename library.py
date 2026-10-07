# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 The ScanBookShelf Authors

"""CLI обслуживания библиотеки: журнал действий, откат, чистка записей."""

import argparse
import os
import sys

from utils import long_path
from config import OUTPUT_BOOKS_FOLDER
from authors import add_alias, create_author, find_author, suggest_merges
from db import DONE_STATUSES, BookDB
from editions import edition_relation, parse_edition, quality_key, same_work, work_key, year_from_text
from library_ops import (LibraryOpError, _genre_move_steps, merge_authors, plan_book_step, prune_missing,
                         resolve_same_edition, run_action, set_author_genre, undo_action)
from reader import probe_book, quality_score


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


def cmd_reclassify(db, args) -> int:
    import llm
    import preflight
    from authors import category_path_for_book
    problems = preflight.check_ollama()
    if problems:
        for p in problems:
            print(f"⛔ {p}")
        return 2
    if args.category:
        path = args.category
        rows = db.conn.execute("SELECT * FROM books WHERE status = 'ok' AND (category = ? OR category LIKE ?) "
                               "ORDER BY id", (path, path + " | %")).fetchall()
    else:
        path = ""
        rows = db.conn.execute("SELECT * FROM books WHERE status = 'needs_review' ORDER BY id").fetchall()
    if args.limit:
        rows = rows[:args.limit]
    steps: list[dict] = []
    try:
        for r in rows:
            decided = llm.decide_category(
                r["preview_text"] or "", r["title"] or "", r["author"] or "",
                r["fb2_genres"].split(",") if r["fb2_genres"] else [],
                r["subjects"].split("; ") if r["subjects"] else [])
            new = decided.get("category", "")
            old = r["category"]
            if new == llm.DEFAULT_CATEGORY:
                print(f"{r['id']} | {r['title']} | {old} → осталась в проверке")
                continue
            fiction = new.startswith("Художественные | ")
            series = r["series"] or ("Без серии" if fiction else "")
            new_path, book_genre = category_path_for_book(db, new, r["author"] or "", series,
                                                          r["author_id"], dry_run=True)
            if new_path == old and r["status"] == "ok":
                continue
            step = plan_book_step(
                db, r["id"], args.output, category=new_path,
                updates={"category_confidence": decided.get("confidence", 0.0),
                         "category_evidence": decided.get("evidence", ""),
                         "category_source": decided.get("source", "llm"),
                         "book_genre": book_genre or "", "status": "ok", "error_reason": ""},
                fuzzy_from_level=3 if r["author_id"] else 2)
            steps.append(step)
            print(f"{r['id']} | {r['title']} | {old} → {new_path} | {decided.get('evidence', '')}")
    except KeyboardInterrupt:
        print("прервано, ничего не изменено")
        return 130
    print(f"изменится {len(steps)} из {len(rows)}")
    if args.apply and steps:
        label = path or "needs_review"
        action_id = run_action(db, "reclassify", f"Переклассификация: {label} ({len(steps)} книг)", steps, args.output)
        print(f"✅ действие #{action_id}")
    elif not args.apply:
        print("(план; добавь --apply)")
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


def cmd_backfill_quality(db, args) -> int:
    marks = ",".join("?" * len(DONE_STATUSES))
    rows = db.conn.execute(
        f"SELECT * FROM books WHERE status IN ({marks}) AND quality IS NULL AND new_path != '' ORDER BY id",
        list(DONE_STATUSES)).fetchall()
    rows = [r for r in rows if r["new_path"] and os.path.isfile(long_path(r["new_path"]))]
    if args.limit:
        rows = rows[:args.limit]
    done = 0
    for r in rows:
        try:
            info = probe_book(r["new_path"])
        except Exception as exc:
            print(f"⚠️ #{r['id']} {r['new_path']}: {exc}")
            continue
        emb = info.embedded or {}
        values = {
            "book_format": info.fmt, "page_count": info.page_count,
            "has_text_layer": 0 if info.ocr_used else 1, "quality": quality_score(info.fmt, info.ocr_used),
        }
        if not r["fb2_genres"] and emb.get("genres"):
            values["fb2_genres"] = ",".join(emb["genres"])
        if not r["subjects"] and emb.get("subjects"):
            values["subjects"] = "; ".join(emb["subjects"])[:500]
        if not r["pub_year"] and emb.get("year"):
            values["pub_year"] = emb["year"]
        if not r["publisher"] and emb.get("publisher"):
            values["publisher"] = emb["publisher"]
        done += 1
        if args.apply:
            db.conn.execute(f"UPDATE books SET {', '.join(c + ' = ?' for c in values)} WHERE id = ?",
                            list(values.values()) + [r["id"]])
            if done % 100 == 0:
                db.conn.commit()
                print(f"… обработано {done} из {len(rows)}")
    if args.apply:
        db.conn.commit()
    print(f"{'Обновлено' if args.apply else 'Будет обновлено (используй --apply)'}: {done}")
    return 0


def cmd_backfill_works(db, args) -> int:
    rows = db.conn.execute(
        "SELECT * FROM books WHERE status IN ('ok', 'needs_review', 'same_edition') ORDER BY id").fetchall()
    for r in rows:
        preview = r["preview_text"] or ""
        edition = r["edition"] or parse_edition(f"{r['title'] or ''} {preview[:3000]}")
        pub_year = r["pub_year"] or year_from_text(preview[:5000])
        key = work_key(r["author_id"], r["author"] or "", r["title"] or "")
        if args.apply:
            db.conn.execute("UPDATE books SET edition = ?, pub_year = ?, work_key = ? WHERE id = ?",
                            (edition, pub_year, key, r["id"]))
    if args.apply:
        db.conn.commit()
    print(f"{'Обновлено' if args.apply else 'Будет обновлено (используй --apply)'}: {len(rows)}")
    return 0


def _clusters(db) -> list[list[dict]]:
    skip = {r["work_key"] for r in db.conn.execute("SELECT work_key FROM work_reviews WHERE decision = 'different'")}
    books = [dict(r) for r in db.conn.execute(
        "SELECT * FROM books WHERE status = 'ok' AND work_key IS NOT NULL AND work_key != '' ORDER BY id")]
    parent = list(range(len(books)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    groups: list[list[int]] = []
    for i, b in enumerate(books):
        if b["work_key"] in skip:
            continue
        for g in groups:
            if same_work(books[g[0]]["work_key"], b["work_key"]):
                g.append(i)
                break
        else:
            groups.append([i])
    for g in groups:
        for x in range(len(g)):
            for y in range(x + 1, len(g)):
                if edition_relation(books[g[x]], books[g[y]]) == "same":
                    parent[find(g[x])] = find(g[y])
    clusters: dict[int, list[dict]] = {}
    for g in groups:
        for i in g:
            clusters.setdefault(find(i), []).append(books[i])
    return [c for c in clusters.values() if len(c) >= 2]


def cmd_dedupe_editions(db, args) -> int:
    clusters = _clusters(db)
    moved = 0
    for cluster in clusters:
        best = max(cluster, key=quality_key)
        rest = [b for b in cluster if b["id"] != best["id"]]
        print(f"Оставить: #{best['id']} {best['title']} | {best['book_format']} | {best['page_count']} стр. | "
              f"{best['source_size']} Б | {best['new_path']}")
        for b in rest:
            print(f"   уходит: #{b['id']} | {b['book_format']} | {b['page_count']} стр. | "
                  f"{b['source_size']} Б | {b['new_path']}")
        moved += len(rest)
        if args.apply:
            resolve_same_edition(db, best["id"], [b["id"] for b in rest], args.output)
    print(f"Кластеров {len(clusters)}, экземпляров в _Дубли {moved}" + ("" if args.apply else " (план; добавь --apply)"))
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
    p = sub.add_parser("reclassify")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--category")
    g.add_argument("--status", choices=["needs_review"])
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--apply", action="store_true")
    p.set_defaults(func=cmd_reclassify)
    p = sub.add_parser("set-author-genre")
    p.add_argument("author_id", type=int)
    p.add_argument("genre")
    p.add_argument("--apply", action="store_true")
    p.set_defaults(func=cmd_set_author_genre)
    p = sub.add_parser("backfill-quality")
    p.add_argument("--apply", action="store_true")
    p.add_argument("--limit", type=int, default=0)
    p.set_defaults(func=cmd_backfill_quality)
    p = sub.add_parser("backfill-works")
    p.add_argument("--apply", action="store_true")
    p.set_defaults(func=cmd_backfill_works)
    p = sub.add_parser("dedupe-editions")
    p.add_argument("--apply", action="store_true")
    p.set_defaults(func=cmd_dedupe_editions)
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
