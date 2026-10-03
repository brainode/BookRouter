# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 The ScanBookShelf Authors

"""List the review queue or confirm a category without calling the LLM."""

import argparse
from pathlib import Path

from config import OUTPUT_BOOKS_FOLDER
from db import BookDB
from llm import ALLOWED_CATEGORIES, DEFAULT_CATEGORY, build_category_path
from utils import copy_book_to_category


def resolve_book(db, book_id, category, output_folder, title=None, author=None, series=None):
    if category not in ALLOWED_CATEGORIES or category == DEFAULT_CATEGORY:
        raise ValueError("Choose a specific category from CATEGORY_TREE")
    row = db.conn.execute("SELECT * FROM books WHERE id = ? AND status = 'needs_review'", (book_id,)).fetchone()
    if row is None:
        raise ValueError(f"Book {book_id} is not in the review queue")
    if not output_folder:
        raise ValueError("Output folder is required")
    source = Path(row["new_path"]).resolve()
    review_root = (Path(output_folder) / DEFAULT_CATEGORY).resolve()
    if not source.is_relative_to(review_root) or not source.is_file():
        raise ValueError("Review copy is missing or outside the review folder")
    title = title if title is not None else row["title"]
    author = author if author is not None else row["author"]
    series = series if series is not None else row["series"] or ""
    category_path = build_category_path(category, author, series)
    destination = copy_book_to_category(str(source), title, author, category_path, output_folder)
    if not destination:
        raise OSError("Could not copy the reviewed book")
    try:
        db.conn.execute(
            "UPDATE books SET title = ?, author = ?, series = ?, category = ?, new_path = ?, "
            "status = 'ok', error_reason = '', metadata_source = 'manual' WHERE id = ?",
            (title, author, series, category_path, destination, book_id),
        )
        db.conn.commit()
    except Exception:
        db.conn.rollback()
        Path(destination).unlink(missing_ok=True)
        raise
    source.unlink()
    return destination


def main(argv=None):
    parser = argparse.ArgumentParser(description="Очередь ручной проверки книг")
    parser.add_argument("--db", default="books.db")
    parser.add_argument("--output", default=OUTPUT_BOOKS_FOLDER)
    parser.add_argument("--resolve", type=int, metavar="ID")
    parser.add_argument("--category", choices=[item for item in ALLOWED_CATEGORIES if item != DEFAULT_CATEGORY])
    parser.add_argument("--title")
    parser.add_argument("--author")
    parser.add_argument("--series")
    args = parser.parse_args(argv)
    if args.resolve is not None and not args.category:
        parser.error("--resolve requires --category")
    if not Path(args.db).is_file():
        parser.error("Database does not exist")
    db = BookDB(args.db)
    try:
        if args.resolve is not None:
            print(resolve_book(db, args.resolve, args.category, args.output, args.title, args.author, args.series))
        else:
            for row in db.conn.execute("SELECT id, title, error_reason, new_path FROM books WHERE status = 'needs_review' ORDER BY id"):
                print(f"{row['id']} | {row['title']} | {row['error_reason']} | {row['new_path']}")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
