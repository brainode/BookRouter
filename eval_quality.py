# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 The ScanBookShelf Authors
"""Оценка качества: эталон (golden), сверка с жанрами FB2, согласованность, прогон промпта."""

import argparse
import random
from collections import Counter, defaultdict
from datetime import datetime

import config
from authors import alias_key
from db import BookDB
from genres import DECISIVE_FB2_GENRES, FICTION_SF
from llm import decide_category, extract_book_facts
from normalization import normalize_for_match
from preflight import check_ollama

FICTION = "Художественные"
FULL_FB2_MAP = {**DECISIVE_FB2_GENRES, "sf": FICTION_SF, "sf_etc": FICTION_SF}


def _parts(path: str) -> list[str]:
    return [p.strip() for p in (path or "").split("|")]


def category_base(path: str) -> str:
    parts = _parts(path)
    if parts and parts[0] == FICTION:
        parts = parts[:2]
    return " | ".join(parts)


def add_golden(db, book_id: int) -> None:
    row = db.conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
    if row is None:
        raise ValueError(f"Книга {book_id} не найдена")
    db.conn.execute(
        "INSERT OR REPLACE INTO golden (book_id, title, author, category, pub_year, edition, verified_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (book_id, row["title"] or "", row["author"] or "", category_base(row["category"] or ""),
         row["pub_year"] or "", row["edition"] or "", datetime.now().isoformat(timespec="seconds")),
    )
    db.conn.commit()


def fb2_agreement(db) -> dict:
    rows = db.conn.execute(
        "SELECT category, book_genre, fb2_genres FROM books WHERE status = 'ok' "
        "AND fb2_genres IS NOT NULL AND fb2_genres != '' AND category LIKE ?", (FICTION + "%",)
    ).fetchall()
    total = agree = specific_total = specific_agree = 0
    confusions: Counter = Counter()
    for r in rows:
        parts = _parts(r["category"])
        genre = r["book_genre"] or (parts[1] if len(parts) > 1 else "")
        tags = [t.strip() for t in r["fb2_genres"].split(",") if t.strip()]
        expected = {FULL_FB2_MAP[t] for t in tags if t in FULL_FB2_MAP}
        expected_genres = {_parts(e)[1] for e in expected if e.startswith(FICTION)}
        total += 1
        ok = genre in expected_genres
        agree += ok
        if not ok:
            confusions[("/".join(sorted(expected_genres)) or "-", genre)] += 1
        spec = {_parts(DECISIVE_FB2_GENRES[t])[1] for t in tags
                if t in DECISIVE_FB2_GENRES and DECISIVE_FB2_GENRES[t].startswith(FICTION)}
        if spec:
            specific_total += 1
            specific_agree += genre in spec
    return {"total": total, "agree": agree, "specific_total": specific_total,
            "specific_agree": specific_agree, "confusions": confusions.most_common(15)}


def consistency(db) -> dict:
    rows = db.conn.execute(
        "SELECT isbn_norm, title, author, category FROM books WHERE status = 'ok'").fetchall()
    groups: list[list[str]] = []
    by_isbn: dict = defaultdict(list)
    by_ta: dict = defaultdict(list)
    for r in rows:
        cat = category_base(r["category"] or "")
        if r["isbn_norm"]:
            by_isbn[r["isbn_norm"]].append(cat)
        by_ta[((r["title"] or "").lower(), (r["author"] or "").lower())].append(cat)
    for g in list(by_isbn.values()) + list(by_ta.values()):
        if len(g) > 1:
            groups.append(g)
    consistent = sum(1 for g in groups if len(set(g)) == 1)
    books = sum(len(g) for g in groups)
    majority = sum(Counter(g).most_common(1)[0][1] for g in groups)
    return {"groups": len(groups),
            "consistent_share": consistent / len(groups) if groups else 1.0,
            "majority_share": majority / books if books else 1.0}


def run_golden(db, limit: int | None = None) -> dict:
    sql = ("SELECT g.*, b.preview_text, b.original_filename, b.fb2_genres, b.subjects "
           "FROM golden g JOIN books b ON b.id = g.book_id ORDER BY g.book_id")
    if limit:
        sql += f" LIMIT {int(limit)}"
    fields = ("title", "author", "category", "pub_year", "edition")
    hits = {f: 0 for f in fields}
    diffs: list = []
    rows = db.conn.execute(sql).fetchall()
    for r in rows:
        text = r["preview_text"] or ""
        facts = extract_book_facts(text, r["original_filename"] or "")
        genres = [g for g in (r["fb2_genres"] or "").split(",") if g]
        subjects = [s for s in (r["subjects"] or "").split("; ") if s]
        decided = decide_category(text, facts.get("title", ""), facts.get("author", ""), genres, subjects)
        got = {"title": facts.get("title", ""), "author": facts.get("author", ""),
               "category": category_base(decided.get("category", "")),
               "pub_year": facts.get("pub_year", ""), "edition": facts.get("edition", "")}
        for f in fields:
            exp = r[f] or ""
            if f == "title":
                same = normalize_for_match(exp) == normalize_for_match(got[f])
            elif f == "author":
                same = alias_key(exp) == alias_key(got[f])
            else:
                same = exp == got[f]
            if same:
                hits[f] += 1
            else:
                diffs.append((r["book_id"], f, exp, got[f]))
    n = len(rows)
    return {"total": n, "accuracy": {f: (hits[f] / n if n else 0.0) for f in fields}, "diffs": diffs}


def sample_for_review(db, n: int) -> list[int]:
    rows = db.conn.execute("SELECT id, category FROM books WHERE status = 'ok'").fetchall()
    levels: dict = defaultdict(list)
    for r in rows:
        levels[_parts(r["category"] or "")[0]].append(r["id"])
    total = sum(len(v) for v in levels.values())
    result: list[int] = []
    for ids in levels.values():
        k = max(1, round(n * len(ids) / total)) if total else 0
        result += random.sample(ids, min(k, len(ids)))
    return result


def _pct(a: int, b: int) -> str:
    return f"{100 * a / b:.0f}%" if b else "-"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Оценка качества классификации")
    parser.add_argument("--db", default="books.db")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("fb2")
    sub.add_parser("consistency")
    p_run = sub.add_parser("run")
    p_run.add_argument("--limit", type=int)
    p_s = sub.add_parser("sample")
    p_s.add_argument("n", type=int)
    args = parser.parse_args(argv)
    db = BookDB(args.db)
    try:
        if args.cmd == "fb2":
            r = fb2_agreement(db)
            print(f"Совпадение с FB2: {r['agree']}/{r['total']} ({_pct(r['agree'], r['total'])})")
            print(f"По конкретным тегам: {r['specific_agree']}/{r['specific_total']} "
                  f"({_pct(r['specific_agree'], r['specific_total'])})")
            for (exp, got), cnt in r["confusions"]:
                print(f"{cnt:5d} | ожидали {exp} | получили {got}")
        elif args.cmd == "consistency":
            r = consistency(db)
            print(f"Групп: {r['groups']}; согласованных: {r['consistent_share']:.0%}; "
                  f"в мажоритарной категории: {r['majority_share']:.0%}")
        elif args.cmd == "run":
            problems = check_ollama()
            if problems:
                print("\n".join(problems))
                return 2
            r = run_golden(db, args.limit)
            print(f"Модель: {config.MODEL_NAME}; книг: {r['total']}")
            for f, v in r["accuracy"].items():
                print(f"{f}: {v:.0%}")
            for d in r["diffs"][:30]:
                print(f"{d[0]} | {d[1]} | эталон={d[2]!r} | получено={d[3]!r}")
        elif args.cmd == "sample":
            for bid in sample_for_review(db, args.n):
                row = db.conn.execute("SELECT title, category FROM books WHERE id = ?", (bid,)).fetchone()
                print(f"{bid} | {row['title']} | {row['category']}")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
