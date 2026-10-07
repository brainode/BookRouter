import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from db import BookDB
from sources import collect_book_sources
from utils import _strip_errors_prefix, copy_to_errors, file_fingerprint, long_path, sanitize_filename

ROOT = Path(__file__).resolve().parent.parent


def _load_main():
    # Модуль называется __main__.py — грузим его под другим именем, чтобы не запускать main()
    spec = importlib.util.spec_from_file_location("bookrouter_main", ROOT / "__main__.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _book_kwargs(**overrides):
    values = dict(
        time_added="2026-10-02 00:00:00",
        original_filename="book.pdf",
        original_path="C:\\in\\book.pdf",
        isbn="",
        preview_text="text",
        title="Title",
        author="Author",
        category="IT",
        new_path="C:\\out\\IT\\book.pdf",
        origin_type="file",
        origin_path="C:\\in\\book.pdf",
        status="ok",
        content_hash="10:abc",
    )
    values.update(overrides)
    return values


@pytest.fixture
def db(tmp_path):
    database = BookDB(str(tmp_path / "books.db"))
    yield database
    database.close()


def test_upsert_updates_existing_row(db):
    first_id = db.upsert_book(**_book_kwargs(status="error_extract", new_path="C:\\out\\Errors\\book.pdf"))
    second_id = db.upsert_book(**_book_kwargs())
    assert first_id == second_id
    assert db.conn.execute("SELECT COUNT(*) FROM books").fetchone()[0] == 1
    assert db.find_book_by_origin("file", "C:\\in\\book.pdf")["status"] == "ok"


def test_origin_keys_skip_only_finished(db):
    db.upsert_book(**_book_kwargs(origin_path="a", status="ok"))
    db.upsert_book(**_book_kwargs(origin_path="b", status="duplicate"))
    db.upsert_book(**_book_kwargs(origin_path="c", status="error_extract"))
    assert db.get_all_origin_keys(only_ok=True) == {("file", "a"), ("file", "b")}
    assert len(db.get_all_origin_keys(only_ok=False)) == 3


def test_ok_hashes(db, tmp_path):
    output = tmp_path / "p1"
    output.write_bytes(b"book")
    db.upsert_book(**_book_kwargs(origin_path="a", content_hash="h1", new_path=str(output)))
    db.upsert_book(**_book_kwargs(origin_path="b", content_hash="h2", status="error_extract"))
    assert db.get_ok_hashes() == {"h1": [str(output)]}
    output.unlink()
    assert db.get_ok_hashes() == {}


def test_confidence_scores_are_stored_independently(db):
    book_id = db.upsert_book(**_book_kwargs(metadata_confidence=0.9, facts_confidence=0.4, category_confidence=0.2))
    row = db.conn.execute("SELECT metadata_confidence, facts_confidence, category_confidence FROM books WHERE id = ?", (book_id,)).fetchone()
    assert tuple(row) == (0.9, 0.4, 0.2)
    db.upsert_book(**_book_kwargs(metadata_confidence=0.8, facts_confidence=0.5, category_confidence=0.3))
    row = db.conn.execute("SELECT metadata_confidence, facts_confidence, category_confidence FROM books WHERE id = ?", (book_id,)).fetchone()
    assert tuple(row) == (0.8, 0.5, 0.3)


def test_csv_export_reflects_current_database_without_duplicate_rows(db, tmp_path):
    import csv
    db.upsert_book(**_book_kwargs(title="Before", category_confidence=0.2))
    path = tmp_path / "results.csv"
    db.export_results(str(path))
    db.upsert_book(**_book_kwargs(title="After", category_confidence=0.9))
    db.export_results(str(path))
    with path.open(encoding="utf-8", newline="") as file:
        rows = list(csv.reader(file, delimiter="|"))
    assert len(rows) == 2
    assert rows[1][3] == "After"
    assert rows[1][16] == "0.9"


def test_migration_adds_columns_to_old_schema(tmp_path):
    import sqlite3

    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE books (id INTEGER PRIMARY KEY AUTOINCREMENT, time_added TEXT NOT NULL, "
        "original_filename TEXT NOT NULL, original_path TEXT NOT NULL, isbn TEXT, preview_text TEXT, "
        "title TEXT, author TEXT, category TEXT, new_path TEXT)"
    )
    conn.commit()
    conn.close()
    database = BookDB(str(path))
    columns = {row["name"] for row in database.conn.execute("PRAGMA table_info(books)")}
    database.close()
    assert {"status", "content_hash", "origin_path"} <= columns


def test_migration_adds_t42_columns(tmp_path):
    import sqlite3

    path = tmp_path / "old42.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE books (id INTEGER PRIMARY KEY AUTOINCREMENT, time_added TEXT NOT NULL, "
                 "original_filename TEXT NOT NULL, original_path TEXT NOT NULL, isbn TEXT, preview_text TEXT, "
                 "title TEXT, author TEXT, category TEXT, new_path TEXT)")
    conn.commit()
    conn.close()
    database = BookDB(str(path))
    columns = {row["name"] for row in database.conn.execute("PRAGMA table_info(books)")}
    database.close()
    assert {"category_evidence", "category_source", "pub_year", "publisher", "edition"} <= columns


@pytest.mark.parametrize(
    "name, expected",
    [
        ("a/b:c?.pdf", "a-b-c-.pdf"),
        ("CON.pdf", "_CON.pdf"),
        ("nul", "_nul"),
        ("title. ", "title"),
        ("", "_"),
    ],
)
def test_sanitize_filename(name, expected):
    assert sanitize_filename(name) == expected


def test_sanitize_filename_truncates_keeping_extension():
    result = sanitize_filename("x" * 300 + ".djvu")
    assert len(result) <= 120 and result.endswith(".djvu")


def test_strip_errors_prefix():
    assert _strip_errors_prefix("Errors/file/Errors/file/Art/book.epub") == "Art/book.epub"
    assert _strip_errors_prefix("Art/Errors/book.epub") == "Art/Errors/book.epub"


@pytest.mark.skipif(os.name != "nt", reason="только Windows")
def test_long_path_prefix():
    short = "C:\\a\\b.pdf"
    assert long_path(short) == short
    assert long_path("C:\\" + "a" * 300).startswith("\\\\?\\C:\\")


def test_copy_to_errors_reuses_copy_and_strips_prefix(tmp_path):
    src_root = tmp_path / "in"
    book = src_root / "Errors" / "file" / "Art" / "book.epub"
    book.parent.mkdir(parents=True)
    book.write_bytes(b"data")
    source = SimpleNamespace(origin_type="file", source_root=str(src_root), logical_path=str(book), display_name="book.epub")
    out = tmp_path / "out"

    assert copy_to_errors(source, str(out), "r", dry_run=True) == str(out / "Errors" / "file" / "Art" / "book.epub")
    assert not out.exists()

    first = copy_to_errors(source, str(out), "r")
    second = copy_to_errors(source, str(out), "r")
    assert first == second
    assert len(list((out / "Errors" / "file" / "Art").iterdir())) == 1


def test_file_fingerprint(tmp_path):
    a = tmp_path / "a.pdf"
    b = tmp_path / "b.pdf"
    c = tmp_path / "c.pdf"
    a.write_bytes(b"same content")
    b.write_bytes(b"same content")
    c.write_bytes(b"other content")
    assert file_fingerprint(str(a)) == file_fingerprint(str(b)) != file_fingerprint(str(c))


def test_collect_sources_excludes_output_inside_input(tmp_path):
    (tmp_path / "books").mkdir()
    (tmp_path / "books" / "a.pdf").write_bytes(b"%PDF")
    (tmp_path / "sorted" / "IT").mkdir(parents=True)
    (tmp_path / "sorted" / "IT" / "b.pdf").write_bytes(b"%PDF")
    sources, _ = collect_book_sources(str(tmp_path), exclude_dirs=(str(tmp_path / "sorted"),))
    assert [source.display_name for source in sources] == ["a.pdf"]


def test_parse_args_and_reason_key():
    main = _load_main()
    options = main.parse_args(["--input", "in", "--output", "out", "--limit", "5", "--dry-run", "--only-ext", "PDF,.djvu"])
    assert (options.input_folder, options.output_folder, options.limit, options.dry_run) == ("in", "out", 5, True)
    assert options.only_ext == {".pdf", ".djvu"}
    assert options.output_csv == "results.dry-run.csv"
    assert main._reason_key("Failed to open file 'C:\\x\\a.pdf' as type pdf.") == main._reason_key(
        "Failed to open file 'D:\\y.pdf' as type pdf."
    )
    assert main._reason_key("djvused_failed rc=10: x") == "djvused_failed rc=N: x"
