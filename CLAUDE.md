# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

BookRouter scans a folder of books (`pdf`, `djvu`/`djv`, `epub`, `fb2`, and those formats inside `.zip` archives), extracts text (text layer first, OCR fallback), identifies title/author/ISBN/series via a local Ollama LLM plus OpenLibrary/Google Books, classifies each book into a fixed category tree, and **copies** (never moves) it into a categorized output folder. The logger is named `bookrouter` (copyright headers still say ScanBookShelf, the former name). User-facing messages, category names and placeholder values are in Russian.

## Commands

```powershell
.\setup_venv.ps1                                  # create .venv, install requirements, copy .env.example -> .env
pip install -r requirements-dev.txt               # + pytest
python -m pytest                                  # all tests (pytest.ini sets pythonpath=. and testpaths=tests)
python -m pytest tests/test_reader.py::test_epub_with_missing_manifest_item   # single test
python __main__.py --help                         # --input/--output/--limit/--dry-run/--only-ext/--[no-]retry-errors
python __main__.py --dry-run --limit 20           # no copies, no book rows; writes results.dry-run.csv
python truncate_db.py [--db books.db] [--vacuum]  # wipe all rows, keep schema
```

There is no linter config or build step. External runtime dependencies: a running Ollama server with `MODEL_NAME` pulled, Tesseract (`TESSERACT_CMD` = path to `tesseract.exe`, a directory is also accepted) with every language from `LANGUAGES`, and DjVuLibre's `djvused`/`djvutxt`/`ddjvu` on `PATH`. `preflight.py` checks all of this after discovery and exits with code 2 before touching anything; then the model is warmed up (`llm.warm_up_model`).

`books.db`, `results.csv` and `app.log` are created in the CWD. To try changes on real books without touching the user's DB, run from a scratch dir: `cd <scratch> && python <repo>/__main__.py --input <in> --output <out> --dry-run --limit N`. `__main__.py` can't be imported as a module by name — tests load it via `importlib.util.spec_from_file_location` (see `tests/test_pipeline.py`).

`TASKS.md` (gitignored, may be absent) is a local log of diagnosed problems, fixes and known limitations.

## Configuration

`config.py` loads `.env` from the repo root by hand (no python-dotenv; real env vars take precedence via `setdefault`) and exposes every setting as a module constant with its default. CLI flags override only input/output/retry. `CATEGORY_TREE` in `config.py` is the single source of truth for allowed categories — `llm.py` parses it (tab- or `|`-separated) into `ALLOWED_CATEGORIES`, which also becomes the JSON-schema enum for the classifier.

## Pipeline architecture

`__main__.py` orchestrates; each book flows through:

1. **Discovery** (`sources.py`): `collect_book_sources` walks the input folder (skipping the output folder if it is nested inside) and produces `BookSource` objects. Zip members get `logical_path = zip://<archive>!<member>` and are validated (path traversal, encryption, size); invalid ones are kept with `status_hint="error_archive"` so they still get recorded.
2. **Selection**: sources whose `(origin_type, logical_path)` already exist in the DB with status `ok`/`duplicate` are skipped (all statuses with `--no-retry-errors`), then `--only-ext`/`--limit` apply. Paths are normalized to backslashes before storing/comparing — keep that consistent.
3. **Extraction** (`reader.py`, in a `ThreadPoolExecutor` with ordered prefetch in `iter_extracted_files`): zip members are materialized to a temp dir (`materialize_zip_member`; release with `cleanup_source`). A fast content fingerprint (`utils.file_fingerprint`: size + sha1 of first/last 4 MB) is checked against already-sorted books — a match becomes `duplicate` with no extraction or LLM. `extract_text_with_ends` picks the reader by file signature (`detect_format`; extensions lie) and returns `(head, tail)` for the first `MAX_PAGES` / last `MAX_TAIL_PAGES`; the tail is only for ISBN, so it is OCR'd only if the head has no ISBN. DjVu uses the `djvutxt` text layer first; OCR when the text is under ~50 words. Pages are rendered for OCR at `OCR_DPI` (300) — native 600 dpi scans make Tesseract take minutes per page; a page timeout skips that page, the book fails only if every page timed out. DjVuLibre on Windows cannot open non-ASCII or long paths, so such files are copied to an ASCII temp path (`prepare_book_path`). EPUB is parsed from OPF/spine directly (missing manifest items are skipped), PyMuPDF is the fallback.
4. **Facts** (`llm.extract_book_facts`): Ollama call with a JSON schema (`think=False` by default — thinking models otherwise spend the whole `LLM_NUM_PREDICT` on reasoning and return empty content), then fallbacks to text heuristics and filename hints (`normalization.parse_filename_hints`). Books with no text but a meaningful filename (`error_no_text`) skip this step and use the filename hints.
5. **Enrichment** (`metadata_enricher.MetadataEnricher`): lookup by ISBN first, then title+author, over the providers in `ENRICH_PROVIDERS`, cached in `metadata_cache` with a TTL. External data only fills in title/author when the local value is empty or an "unknown" label; it does override ISBN/series.
6. **Classification** (`llm.classify_category`) → `build_category_path`: fiction (`Художественные | …`) gets extra `author-slug | series-slug` levels; the author is canonicalized first (`normalization.canonical_author_name` drops patronymics/middle initials).
7. **Copy** (`utils.copy_book_to_category`): category parts become nested folders; existing folders that fuzzy-match (`PATH_ALIAS_THRESHOLD`) or have the same words in another order (`token_key`) are reused. Failures are routed through `utils.copy_to_errors` into `<OUTPUT>/<ERRORS_SUBFOLDER>/{file|zip}/…` (an identical existing copy is reused). All filesystem calls on output paths go through `utils.long_path` (Windows `\\?\` prefix) and `sanitize_filename` (reserved names, trailing dots, length).
8. **Persist**: `_store_result` UPSERTs into `books.db` (`BookDB.upsert_book`, keyed by origin) and removes a stale `Errors` copy when a retried book succeeds; every result is appended to `results.csv` (preview text truncated). A status/error-reason summary is printed at the end.

Stage functions return plain dicts with a `status` field rather than raising. Statuses: `ok`, `duplicate`, `error_extract`, `error_archive`, `error_no_text`, `error_process`, `error_transient` (Ollama unavailable — not copied to `Errors`, retried next run; 3 in a row stop the run), `interrupted` (never stored), `fatal` (`OCRConfigError` — environment broken, the run stops). `_error_result` in `__main__.py` defines the full result shape that `_store_result` and `_csv_row` expect.

## Conventions and gotchas

- Interruption: the signal handler (SIGINT/SIGTERM/SIGBREAK) only sets `interrupt.stop_event`; readers call `check_interrupted()` between pages and the main loop polls futures with a timeout. DjVuLibre subprocesses run in their own process group so Ctrl+C doesn't kill them mid-page. Don't call `sys.exit` from signal handlers.
- `logging_utils.install_print_logging` monkey-patches `builtins.print` to log at DEBUG to `app.log` (and the console if `LOG_TO_CONSOLE`), so `print` calls are effectively log statements.
- Errors from external tools must not be swallowed into empty text: raise `OCRConfigError` (environment), `OCRTimeoutError` or `ExtractError` (this book) from `reader.py`.
- `llm._chat_raw` is the single entry point to Ollama: client timeout `LLM_TIMEOUT_SEC`, connection/5xx errors → `LLMUnavailableError`, 4xx/`TypeError` → retry with fewer parameters (`think` → `keep_alive` → `format`).
- DB schema changes: add new `books` columns to `BOOK_COLUMN_MIGRATIONS` in `db.py` (applied via `ALTER TABLE` on startup) as well as `CREATE_TABLE_SQL` and both statements in `upsert_book`. `books_fts` is an FTS virtual table kept in sync by triggers.
- New metadata providers subclass `providers.base.MetadataProvider`, return `ProviderResult`, and must be wired into `MetadataEnricher._build_providers` by name.
- Every source file starts with the SPDX `GPL-2.0-only` header.
- `books.db`, `app.log`, `*.csv` and `.env` are local runtime artifacts (gitignored).
