# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 The ScanBookShelf Authors
import eval_quality
import review_books
from db import BookDB


def _add(db, **kw):
    vals = {"time_added": "t", "original_filename": "f.fb2", "original_path": "p", "status": "ok",
            "title": "T", "author": "A", "category": "", "fb2_genres": "", "preview_text": "x",
            "pub_year": "", "edition": ""}
    vals.update(kw)
    cols = ", ".join(vals)
    cur = db.conn.execute(f"INSERT INTO books ({cols}) VALUES ({', '.join('?' * len(vals))})", list(vals.values()))
    db.conn.commit()
    return cur.lastrowid


def test_category_base():
    assert eval_quality.category_base("Художественные | Ужасы | stephen-king | bez-serii") == "Художественные | Ужасы"
    assert eval_quality.category_base("IT | Базы данных | SQL") == "IT | Базы данных | SQL"


def test_fb2_agreement_counts(tmp_path):
    db = BookDB(str(tmp_path / "b.db"))
    _add(db, fb2_genres="sf_horror", category="Художественные | Ужасы | a | b")
    _add(db, fb2_genres="sf", category="Художественные | Фэнтези | a | b")
    _add(db, fb2_genres="child_tale", category="Художественные | Детская | a | b")
    r = eval_quality.fb2_agreement(db)
    assert (r["agree"], r["total"], r["specific_total"]) == (2, 3, 2)


def test_run_golden_compares_fields(tmp_path, monkeypatch):
    db = BookDB(str(tmp_path / "b.db"))
    for i in (1, 2):
        _add(db, title=f"Книга {i}", author="Иван Петров", category="IT | Базы данных", pub_year="2000")
        eval_quality.add_golden(db, i)
    monkeypatch.setattr(eval_quality, "extract_book_facts", lambda t, f: {
        "title": "Книга 1" if f else "", "author": "Иван Петров", "pub_year": "1999", "edition": ""})
    monkeypatch.setattr(eval_quality, "decide_category", lambda *a: {"category": "IT | Базы данных"})
    r = eval_quality.run_golden(db)
    assert r["total"] == 2
    assert r["accuracy"]["title"] == 0.5
    assert r["accuracy"]["author"] == 1.0
    assert r["accuracy"]["category"] == 1.0
    assert r["accuracy"]["pub_year"] == 0.0
    assert (2, "title", "Книга 2", "Книга 1") in r["diffs"]


def test_verify_adds_golden(tmp_path):
    path = str(tmp_path / "b.db")
    db = BookDB(path)
    _add(db, category="Художественные | Ужасы | a | b")
    db.close()
    assert review_books.main(["--db", path, "--verify", "1"]) == 0
    db = BookDB(path)
    row = db.conn.execute("SELECT category FROM golden WHERE book_id = 1").fetchone()
    assert row["category"] == "Художественные | Ужасы"
