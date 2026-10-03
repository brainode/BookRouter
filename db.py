# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 The ScanBookShelf Authors

import sqlite3
import time
from typing import Optional, Tuple

DB_FILE = "books.db"

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS books (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    time_added TEXT NOT NULL,
    original_filename TEXT NOT NULL,
    original_path TEXT NOT NULL,
    origin_type TEXT,
    origin_path TEXT,
    archive_path TEXT,
    archive_member TEXT,
    isbn TEXT,
    isbn_norm TEXT,
    preview_text TEXT,
    title TEXT,
    title_raw TEXT,
    author TEXT,
    author_raw TEXT,
    series TEXT,
    series_index TEXT,
    category TEXT,
    metadata_source TEXT,
    metadata_confidence REAL,
    provider_match_score REAL,
    status TEXT,
    error_reason TEXT,
    new_path TEXT,
    content_hash TEXT,
    category_confidence REAL,
    facts_confidence REAL
);
"""

CREATE_FTS_SQL = """
CREATE VIRTUAL TABLE IF NOT EXISTS books_fts
USING fts5(
    title,
    author,
    preview_text,
    content='books',
    content_rowid='id'
);
"""

CREATE_TRIGGERS_SQL = """
CREATE TRIGGER IF NOT EXISTS books_ai AFTER INSERT ON books BEGIN
  INSERT INTO books_fts(rowid, title, author, preview_text)
  VALUES (new.id, new.title, new.author, new.preview_text);
END;

CREATE TRIGGER IF NOT EXISTS books_ad AFTER DELETE ON books BEGIN
  INSERT INTO books_fts(books_fts, rowid, title, author, preview_text)
  VALUES('delete', old.id, old.title, old.author, old.preview_text);
END;

CREATE TRIGGER IF NOT EXISTS books_au AFTER UPDATE ON books BEGIN
  INSERT INTO books_fts(books_fts, rowid, title, author, preview_text)
  VALUES('delete', old.id, old.title, old.author, old.preview_text);

  INSERT INTO books_fts(rowid, title, author, preview_text)
  VALUES (new.id, new.title, new.author, new.preview_text);
END;
"""

CREATE_METADATA_CACHE_SQL = """
CREATE TABLE IF NOT EXISTS metadata_cache (
    query_key TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    fetched_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL
);
"""

CREATE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_books_isbn_norm ON books(isbn_norm);
CREATE INDEX IF NOT EXISTS idx_books_title_author ON books(title, author);
CREATE INDEX IF NOT EXISTS idx_books_origin ON books(origin_type, origin_path);
CREATE INDEX IF NOT EXISTS idx_cache_expires_at ON metadata_cache(expires_at);
CREATE INDEX IF NOT EXISTS idx_books_content_hash ON books(content_hash);
"""

BOOK_COLUMN_MIGRATIONS = {
    "origin_type": "TEXT",
    "origin_path": "TEXT",
    "archive_path": "TEXT",
    "archive_member": "TEXT",
    "isbn_norm": "TEXT",
    "title_raw": "TEXT",
    "author_raw": "TEXT",
    "series": "TEXT",
    "series_index": "TEXT",
    "metadata_source": "TEXT",
    "metadata_confidence": "REAL",
    "provider_match_score": "REAL",
    "status": "TEXT",
    "error_reason": "TEXT",
    "content_hash": "TEXT",
    "category_confidence": "REAL",
    "facts_confidence": "REAL",
}


class BookDB:
    def __init__(self, db_file: str = DB_FILE):
        self.db_file = db_file
        self.conn = sqlite3.connect(self.db_file)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON;")
        self.conn.execute("PRAGMA journal_mode = WAL;")
        self._init_db()

    def _init_db(self):
        cursor = self.conn.cursor()
        cursor.executescript(CREATE_TABLE_SQL)
        self._ensure_book_columns()
        cursor.executescript(CREATE_FTS_SQL)
        cursor.executescript(CREATE_TRIGGERS_SQL)
        cursor.executescript(CREATE_METADATA_CACHE_SQL)
        cursor.executescript(CREATE_INDEX_SQL)
        self.conn.commit()

    def _ensure_book_columns(self):
        cursor = self.conn.cursor()
        cursor.execute("PRAGMA table_info(books)")
        existing = {row["name"] for row in cursor.fetchall()}

        for column, sql_type in BOOK_COLUMN_MIGRATIONS.items():
            if column not in existing:
                cursor.execute(f"ALTER TABLE books ADD COLUMN {column} {sql_type}")

    def upsert_book(
        self,
        time_added: str,
        original_filename: str,
        original_path: str,
        isbn: Optional[str],
        preview_text: Optional[str],
        title: Optional[str],
        author: Optional[str],
        category: Optional[str],
        new_path: Optional[str],
        origin_type: Optional[str] = None,
        origin_path: Optional[str] = None,
        archive_path: Optional[str] = None,
        archive_member: Optional[str] = None,
        isbn_norm: Optional[str] = None,
        title_raw: Optional[str] = None,
        author_raw: Optional[str] = None,
        series: Optional[str] = None,
        series_index: Optional[str] = None,
        metadata_source: Optional[str] = None,
        metadata_confidence: Optional[float] = None,
        provider_match_score: Optional[float] = None,
        status: Optional[str] = None,
        error_reason: Optional[str] = None,
        content_hash: Optional[str] = None,
        category_confidence: Optional[float] = None,
        facts_confidence: Optional[float] = None,
    ) -> int:
        cursor = self.conn.cursor()
        existing = self.find_book_by_origin(origin_type, origin_path)
        if existing:
            # Повторная обработка (например, после ошибки) обновляет строку, а не плодит дубль
            cursor.execute(
                """
                UPDATE books SET
                    time_added = ?, original_filename = ?, original_path = ?, origin_type = ?,
                    origin_path = ?, archive_path = ?, archive_member = ?, isbn = ?, isbn_norm = ?,
                    preview_text = ?, title = ?, title_raw = ?, author = ?, author_raw = ?,
                    series = ?, series_index = ?, category = ?, metadata_source = ?,
                    metadata_confidence = ?, provider_match_score = ?, status = ?,
                    error_reason = ?, new_path = ?, content_hash = ?, category_confidence = ?, facts_confidence = ?
                WHERE id = ?
                """,
                (
                    time_added,
                    original_filename,
                    original_path,
                    origin_type,
                    origin_path,
                    archive_path,
                    archive_member,
                    isbn,
                    isbn_norm,
                    preview_text,
                    title,
                    title_raw,
                    author,
                    author_raw,
                    series,
                    series_index,
                    category,
                    metadata_source,
                    metadata_confidence,
                    provider_match_score,
                    status,
                    error_reason,
                    new_path,
                    content_hash,
                    category_confidence,
                    facts_confidence,
                    existing["id"],
                ),
            )
            self.conn.commit()
            return existing["id"]

        cursor.execute(
            """
            INSERT INTO books (
                time_added, original_filename, original_path, origin_type,
                origin_path, archive_path, archive_member, isbn, isbn_norm,
                preview_text, title, title_raw, author, author_raw,
                series, series_index, category, metadata_source,
                metadata_confidence, provider_match_score, status,
                error_reason, new_path, content_hash, category_confidence, facts_confidence
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                time_added,
                original_filename,
                original_path,
                origin_type,
                origin_path,
                archive_path,
                archive_member,
                isbn,
                isbn_norm,
                preview_text,
                title,
                title_raw,
                author,
                author_raw,
                series,
                series_index,
                category,
                metadata_source,
                metadata_confidence,
                provider_match_score,
                status,
                error_reason,
                new_path,
                content_hash,
                category_confidence,
                facts_confidence,
            ),
        )
        self.conn.commit()
        return cursor.lastrowid

    def search_books(self, query: str, limit: int = 10) -> list[Tuple]:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT b.id, b.title, b.author, b.series, b.isbn, b.original_filename, b.original_path, b.new_path
            FROM books_fts f
            JOIN books b ON b.id = f.rowid
            WHERE books_fts MATCH ?
            LIMIT ?
            """,
            (query, limit),
        )
        return [tuple(row) for row in cursor.fetchall()]

    def get_all_origin_keys(self, only_ok: bool = True) -> set[tuple[str, str]]:
        """Ключи уже обработанных источников. При only_ok ошибочные не возвращаются — их обработают заново."""
        cursor = self.conn.cursor()
        cursor.execute(
            f"""
            SELECT origin_type, origin_path
            FROM books
            WHERE origin_path IS NOT NULL AND origin_path != ''
            {"AND status IN ('ok', 'duplicate', 'needs_review')" if only_ok else ""}
            """
        )
        return {(str(row["origin_type"] or ""), str(row["origin_path"] or "")) for row in cursor.fetchall()}

    def get_review_origin_keys(self) -> set[tuple[str, str]]:
        return {(row["origin_type"] or "", row["origin_path"] or "") for row in self.conn.execute(
            "SELECT origin_type, origin_path FROM books WHERE status = 'needs_review'"
        )}

    def get_ok_hashes(self) -> dict[str, str]:
        """отпечаток содержимого → путь в библиотеке для успешно разложенных книг."""
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT content_hash, new_path
            FROM books
            WHERE status = 'ok' AND content_hash IS NOT NULL AND content_hash != ''
            ORDER BY id
            """
        )
        hashes: dict[str, str] = {}
        for row in cursor.fetchall():
            hashes.setdefault(row["content_hash"], row["new_path"] or "")
        return hashes

    def find_book_by_origin(self, origin_type: Optional[str], origin_path: Optional[str]) -> dict | None:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT id, status, new_path
            FROM books
            WHERE origin_type = ? AND origin_path = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (origin_type, origin_path),
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def get_cached_metadata(self, query_key: str) -> dict | None:
        now = int(time.time())
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT query_key, provider, payload_json, fetched_at, expires_at
            FROM metadata_cache
            WHERE query_key = ? AND expires_at > ?
            """,
            (query_key, now),
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def upsert_cached_metadata(self, query_key: str, provider: str, payload_json: str, ttl_days: int = 90):
        now = int(time.time())
        expires_at = now + int(max(1, ttl_days)) * 24 * 60 * 60
        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT INTO metadata_cache (query_key, provider, payload_json, fetched_at, expires_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(query_key) DO UPDATE SET
                provider = excluded.provider,
                payload_json = excluded.payload_json,
                fetched_at = excluded.fetched_at,
                expires_at = excluded.expires_at
            """,
            (query_key, provider, payload_json, now, expires_at),
        )
        self.conn.commit()

    def close(self):
        self.conn.close()
