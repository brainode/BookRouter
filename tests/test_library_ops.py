# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 The ScanBookShelf Authors

import os

import pytest

from db import BookDB
from library_ops import (
    LibraryOpError, apply_steps, plan_book_step, prune_missing, run_action, trash_book, undo_action,
)
from utils import category_destination, copy_book_to_category


@pytest.fixture
def env(tmp_path):
    out = tmp_path / "out"
    folder = out / "IT" / "Data Science"
    folder.mkdir(parents=True)
    f = folder / "Book - Author.pdf"
    f.write_bytes(b"data")
    db = BookDB(str(tmp_path / "t.db"))
    bid = db.upsert_book(
        "2026-01-01", "Book.pdf", str(tmp_path / "src" / "Book.pdf"), None, "x", "Book", "Author",
        "IT | Data Science", str(f), origin_type="file", origin_path=str(tmp_path / "src" / "Book.pdf"),
        status="ok",
    )
    return tmp_path, str(out), db, bid, f


def _row(db, bid):
    return db.conn.execute("SELECT * FROM books WHERE id=?", (bid,)).fetchone()


def test_move_and_undo(env):
    tmp, out, db, bid, f = env
    step = plan_book_step(db, bid, out, category="IT | AI и ML")
    aid = run_action(db, "move", "m", [step], out)
    new = os.path.join(out, "IT", "AI и ML", "Book - Author.pdf")
    assert os.path.isfile(new)
    assert _row(db, bid)["category"] == "IT | AI и ML"
    assert not (tmp / "out" / "IT" / "Data Science").exists()
    undo_action(db, aid, out)
    assert f.is_file()
    assert _row(db, bid)["category"] == "IT | Data Science"
    assert db.conn.execute("SELECT undone_at FROM actions_log").fetchone()[0]


def test_undo_twice_fails(env):
    _, out, db, bid, _ = env
    aid = run_action(db, "move", "m", [plan_book_step(db, bid, out, category="IT | AI и ML")], out)
    undo_action(db, aid, out)
    with pytest.raises(LibraryOpError):
        undo_action(db, aid, out)


def test_undo_blocked_by_later_action(env):
    _, out, db, bid, _ = env
    a1 = run_action(db, "move", "m", [plan_book_step(db, bid, out, category="IT | AI и ML")], out)
    run_action(db, "move", "m2", [plan_book_step(db, bid, out, category="IT | Сети")], out)
    with pytest.raises(LibraryOpError):
        undo_action(db, a1, out)


def test_apply_steps_rolls_back_on_error(env):
    _, out, db, bid, f = env
    s1 = plan_book_step(db, bid, out, category="IT | AI и ML")
    s2 = {"kind": "book", "book_id": 9999, "from": "x", "to": "y", "updates": {}}
    with pytest.raises(LibraryOpError):
        apply_steps(db, [s1, s2], out)
    assert f.is_file()
    assert _row(db, bid)["category"] == "IT | Data Science"


def test_trash_book(env):
    _, out, db, bid, _ = env
    trash_book(db, bid, out)
    assert os.path.isfile(os.path.join(out, "_Корзина", "IT", "Data Science", "Book - Author.pdf"))
    row = _row(db, bid)
    assert row["status"] == "trashed"
    assert ("file", row["origin_path"]) in db.get_all_origin_keys()


def test_row_step_undo(env):
    _, out, db, _, _ = env
    step = {"kind": "row", "table": "metadata_cache", "key": "query_key", "key_value": "k", "before": None,
            "after": {"query_key": "k", "provider": "p", "payload_json": "{}", "fetched_at": 1, "expires_at": 2}}
    aid = run_action(db, "row", "r", [step], out)
    assert db.conn.execute("SELECT COUNT(*) FROM metadata_cache").fetchone()[0] == 1
    undo_action(db, aid, out)
    assert db.conn.execute("SELECT COUNT(*) FROM metadata_cache").fetchone()[0] == 0


def test_prune_missing(env):
    tmp, out, db, bid, _ = env
    eid = db.upsert_book(
        "2026-01-01", "E.pdf", "e", None, "", "E", "A", "x", None, origin_type="file",
        origin_path=str(tmp / "gone.pdf"), status="error_extract",
    )
    assert [r["id"] for r in prune_missing(db, False)] == [eid]
    prune_missing(db, True)
    assert db.conn.execute("SELECT id FROM books WHERE id=?", (eid,)).fetchone() is None
    assert _row(db, bid) is not None


def test_category_destination_matches_copy(tmp_path):
    out = str(tmp_path / "out")
    args = ("a.pdf", "T", "A", "Художественные | Ужасы | king | bez-serii", out)
    assert category_destination(*args)[0] == copy_book_to_category(*args, dry_run=True)


def test_set_author_genre_moves_and_undo(tmp_path):
    from authors import create_author
    from library_ops import set_author_genre
    out = tmp_path / "out"
    db = BookDB(str(tmp_path / "t.db"))
    author = create_author(db, "Иван Петров", slug="petrov")
    ids, files = [], []
    for i, genre in enumerate(["Ужасы", "Детектив"]):
        d = out / "Художественные" / genre / "petrov" / "Без серии"
        d.mkdir(parents=True)
        f = d / f"b{i}.pdf"
        f.write_bytes(b"x%d" % i)
        files.append(f)
        ids.append(db.upsert_book(
            "2026-01-01", f"b{i}.pdf", "p", None, "x", f"b{i}", "Иван Петров",
            f"Художественные | {genre} | petrov | Без серии", str(f), origin_type="file",
            origin_path=f"o{i}", status="ok", author_id=author["id"], book_genre=genre))
    aid = set_author_genre(db, author["id"], "Ужасы", str(out))
    row = _row(db, ids[1])
    assert row["category"].split(" | ")[1] == "Ужасы"
    assert os.path.isfile(row["new_path"]) and not files[1].exists()
    assert db.conn.execute("SELECT genre FROM authors WHERE id=?", (author["id"],)).fetchone()[0] == "Ужасы"
    undo_action(db, aid, str(out))
    assert files[1].exists()
    assert _row(db, ids[1])["category"].split(" | ")[1] == "Детектив"
    assert db.conn.execute("SELECT genre FROM authors WHERE id=?", (author["id"],)).fetchone()[0] is None
    with pytest.raises(LibraryOpError):
        set_author_genre(db, author["id"], "Нет такого", str(out))
