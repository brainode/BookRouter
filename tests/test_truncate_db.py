import sqlite3

import pytest

from db import BookDB
from truncate_db import get_regular_tables, truncate_db


@pytest.mark.parametrize("vacuum", [False, True])
def test_truncate_preserves_fts_and_allows_reuse(tmp_path, vacuum):
    path = tmp_path / "books.db"
    database = BookDB(str(path))
    conn = database.conn
    insert = (
        "INSERT INTO books(time_added, original_filename, original_path, title) "
        "VALUES ('now', 'book.pdf', 'book.pdf', ?)"
    )
    conn.execute(insert, ("Before",))
    conn.execute(
        "INSERT INTO metadata_cache VALUES ('query', 'provider', '{}', 1, 2)"
    )
    conn.commit()
    schema = [tuple(row) for row in conn.execute("SELECT name, sql FROM sqlite_master ORDER BY name")]
    assert get_regular_tables(conn) == ["actions_log", "books", "metadata_cache"]
    database.close()

    truncate_db(path, vacuum=vacuum)

    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert conn.execute("SELECT COUNT(*) FROM books").fetchone() == (0,)
        assert conn.execute("SELECT COUNT(*) FROM metadata_cache").fetchone() == (0,)
        assert conn.execute(
            "SELECT rowid FROM books_fts WHERE books_fts MATCH 'Before'"
        ).fetchall() == []
        assert conn.execute("SELECT name, sql FROM sqlite_master ORDER BY name").fetchall() == schema
        assert conn.execute(insert, ("After",)).lastrowid == 1
        assert conn.execute(
            "SELECT rowid FROM books_fts WHERE books_fts MATCH 'After'"
        ).fetchall() == [(1,)]
        conn.execute("INSERT INTO books_fts(books_fts, rank) VALUES ('integrity-check', 1)")


def test_truncate_without_autoincrement(tmp_path):
    path = tmp_path / "plain.db"
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE "quoted""table" (value TEXT)')
        conn.execute('INSERT INTO "quoted""table" VALUES (\'value\')')

    truncate_db(path)

    with sqlite3.connect(path) as conn:
        assert conn.execute('SELECT COUNT(*) FROM "quoted""table"').fetchone() == (0,)
