---
name: pipeline-dev
description: Conventions for changing the BookRouter sorting pipeline, DB schema, LLM prompts and library file operations (__main__.py, reader.py, llm.py, db.py, utils.py, normalization.py, library_ops.py, editions.py, authors.py). Load before editing any of these files.
---

# BookRouter pipeline development

## Result dicts, not exceptions
Stage functions return plain dicts with a `status`. `__main__._error_result` defines the full shape of a book result; every new key must be added there and in the success dict in `process_file`, otherwise `_store_result` raises `KeyError`. Optional keys are read with `result.get(...)`.

Statuses: `ok`, `needs_review`, `duplicate`, `same_edition` (after T50), `error_extract`, `error_archive`, `error_no_text`, `error_process`, `error_transient`, `interrupted`, `fatal`. If you add one, update `_attach_error_fallback`, the counters in `main`, and `BookDB.get_all_origin_keys`.

## DB schema change checklist
1. `db.CREATE_TABLE_SQL` — add the column.
2. `db.BOOK_COLUMN_MIGRATIONS` — same name and type (existing DBs get it via `ALTER TABLE`).
3. `BookDB.upsert_book` — new keyword parameter with default `None`, and the column in **both** the UPDATE and the INSERT statement (count the `?`).
4. `__main__._store_result` — pass the value.
5. New tables: add a `CREATE_..._SQL` constant and execute it in `BookDB._init_db`. Use `CREATE TABLE IF NOT EXISTS`.
6. Test: open an old-schema DB (create `books` without the column by hand) and check the migration adds it.

## Errors from external tools
`reader.py` raises `OCRConfigError` (environment broken → whole run stops), `OCRTimeoutError` or `ExtractError` (this book). Never turn a failure into empty text.

## LLM
`llm._chat_raw` is the only call into Ollama. JSON schema for every call. Prompts live in `prompts/*.txt` after T40 — read them through `config` constants, never inline new prompt text in Python. Tests never call Ollama: monkeypatch `llm._chat_json`.

## Files in the library
- Every filesystem call on an output path goes through `utils.long_path(...)`; every name segment through `utils.sanitize_filename(...)`.
- The first copy is `utils.copy_book_to_category`. Every later move/rename/trash of a book goes through `library_ops` (T48) so that it is journaled in `actions_log` and can be undone. Never delete a book file; move it to `_Корзина` or `_Дубли`.
- Paths stored in the DB use backslashes (`str(path).replace("/", "\\")`).

## Interruption and logging
Readers call `interrupt.check_interrupted()` between pages. Signal handlers only set `stop_event`. `print` is patched to log at DEBUG — use `print` for progress lines (Russian, with the existing emoji style) and `logger = logging.getLogger("bookrouter")` for diagnostics.

## Style
SPDX header `# SPDX-License-Identifier: GPL-2.0-only` + `# Copyright (C) 2026 The ScanBookShelf Authors` on new `.py` files. User-facing text in Russian. Type hints on new functions. No new third-party dependency unless the task names it (then add it to `requirements.txt`).

## Running against real books
Never against the user's `books.db`. From a scratch dir: `cd <scratch> && python <repo>/__main__.py --input <in> --output <out> --dry-run --limit N`.
