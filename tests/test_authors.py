# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 The ScanBookShelf Authors

from authors import create_author, name_parts, phonetic_key, resolve_author
from db import BookDB
from library_ops import merge_authors, undo_action


def test_name_parts():
    assert name_parts("К. И. Чуковский") == ("К", "Чуковский", True)
    assert name_parts("Корней Чуковский") == ("Корней", "Чуковский", False)
    assert name_parts("Чуковский К. И.") == ("К", "Чуковский", True)


def test_phonetic_key():
    assert phonetic_key("Stephen King") == phonetic_key("Стивен Кинг")
    assert phonetic_key("Harry Harrison") == phonetic_key("Гарри Гаррисон")
    assert phonetic_key("Иванов") != phonetic_key("Иванова")


def test_resolve_by_initials_unique(tmp_path):
    db = BookDB(str(tmp_path / "t.db"))
    first = create_author(db, "Корней Чуковский")
    assert resolve_author(db, "К. И. Чуковский")["id"] == first["id"]
    nik = create_author(db, "Николай Чуковский")
    assert resolve_author(db, "Н. Чуковский")["id"] == nik["id"]
    db2 = BookDB(str(tmp_path / "t2.db"))
    a = create_author(db2, "Корней Чуковский")
    b = create_author(db2, "Кирилл Чуковский")
    new = resolve_author(db2, "К. И. Чуковский")
    assert new["id"] not in (a["id"], b["id"])


def test_resolve_same_words_other_order(tmp_path):
    db = BookDB(str(tmp_path / "t.db"))
    a = resolve_author(db, "Николай Носов")
    assert resolve_author(db, "Носов Николай")["id"] == a["id"]
    assert resolve_author(db, "Неизвестный автор") is None


def test_merge_authors_moves_fiction_and_undo(tmp_path):
    db = BookDB(str(tmp_path / "t.db"))
    out = tmp_path / "out"
    target = create_author(db, "Stephen King")
    source = create_author(db, "Стивен Кинг", slug="stiven-king")
    folder = out / "Художественные" / "Ужасы" / "stiven-king" / "bez-serii"
    folder.mkdir(parents=True)
    f = folder / "Оно - Стивен Кинг.pdf"
    f.write_bytes(b"data")
    bid = db.upsert_book("2026-01-01", "a.pdf", "a", None, "x", "Оно", "Стивен Кинг",
                         "Художественные | Ужасы | stiven-king | bez-serii", str(f),
                         origin_type="file", origin_path="a", status="ok", author_id=source["id"])
    aid = merge_authors(db, target["id"], [source["id"]], str(out))
    row = db.conn.execute("SELECT * FROM books WHERE id=?", (bid,)).fetchone()
    assert row["author_id"] == target["id"]
    assert "stephen-king" in row["new_path"] and "bez-serii" in row["new_path"]
    assert (out / "Художественные" / "Ужасы" / "stephen-king" / "bez-serii" / "Оно - Стивен Кинг.pdf").exists()
    assert db.conn.execute("SELECT 1 FROM authors WHERE id=?", (source["id"],)).fetchone() is None
    assert resolve_author(db, "Стивен Кинг")["id"] == target["id"]
    undo_action(db, aid, str(out))
    assert f.exists()
    row = db.conn.execute("SELECT * FROM books WHERE id=?", (bid,)).fetchone()
    assert row["author_id"] == source["id"]
    assert db.conn.execute("SELECT 1 FROM authors WHERE id=?", (source["id"],)).fetchone()
    alias = db.conn.execute("SELECT author_id FROM author_aliases WHERE name='Стивен Кинг'").fetchone()
    assert alias["author_id"] == source["id"]
