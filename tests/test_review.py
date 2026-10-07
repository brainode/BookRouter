# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 The ScanBookShelf Authors

from pathlib import Path

import pytest

from db import BookDB
from review_books import resolve_book


def test_manual_resolution_updates_database_and_keeps_input(tmp_path):
    output = tmp_path / "out"
    review = output / "Требует внимания" / "book.pdf"
    review.parent.mkdir(parents=True)
    review.write_bytes(b"book")
    original = tmp_path / "original.pdf"
    original.write_bytes(b"book")
    db = BookDB(str(tmp_path / "books.db"))
    try:
        db.conn.execute(
            "INSERT INTO books(time_added, original_filename, original_path, origin_type, origin_path, "
            "title, author, category, status, new_path) VALUES ('now', 'book.pdf', ?, 'file', ?, 'Title', 'Author', "
            "'Требует внимания', 'needs_review', ?)",
            (str(original), str(original), str(review)),
        )
        db.conn.commit()
        assert db.get_all_origin_keys() == db.get_review_origin_keys()
        destination = resolve_book(db, 1, "IT | Data Science", str(output), title="Corrected")
        assert Path(destination).read_bytes() == b"book"
        assert not review.exists()
        assert original.exists()
        row = db.conn.execute("SELECT status, title, metadata_source FROM books").fetchone()
        assert tuple(row) == ("ok", "Corrected", "manual")
        assert db.get_review_origin_keys() == set()
        with pytest.raises(ValueError, match="not in the review queue"):
            resolve_book(db, 1, "IT | Data Science", str(output))
    finally:
        db.close()
