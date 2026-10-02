# BookRouter

![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)
![AI Assisted](https://img.shields.io/badge/AI-Assisted-0A7E8C)

BookRouter scans a folder of books, extracts text from supported formats, enriches metadata with AI and public catalog providers, and copies the books into a categorized output bookshelf.

## Features

- Scans local folders for `pdf`, `djvu`, `djv`, `epub`, `fb2`, and ZIP-contained books; the format is detected by file signature, not extension.
- Extracts text from the text layer (PDF, DjVu via `djvutxt`) and falls back to OCR for scans.
- Enriches title, author, ISBN, and series using local heuristics plus OpenLibrary and Google Books.
- Persists processing state in SQLite: finished books are skipped, failed ones are retried on the next run.
- Detects duplicate files and copies each book into category-based folders only once.
- Checks the environment (folders, Tesseract languages, DjVuLibre, Ollama model) before processing.

## Requirements

- Python 3.11 or newer.
- Tesseract OCR installed locally if OCR is enabled, with every language from `LANGUAGES` (default `eng+rus`).
- DjVuLibre tools `djvused`, `djvutxt` and `ddjvu` in `PATH` if you process `djvu` or `djv` files.
- A running Ollama server with the model from `MODEL_NAME` (default `gemma3:12b`) pulled.

The project imports these Python packages:

- `beautifulsoup4`
- `ollama`
- `Pillow`
- `PyMuPDF`
- `pytesseract`

## Setup

1. Create a virtual environment and install dependencies.
2. Copy `.env.example` to `.env` if it was not created automatically.
3. Fill in local paths and optional secrets in `.env`.

Fast setup on Unix-like shells:

```sh
sh ./setup_venv.sh
```

Fast setup on PowerShell:

```powershell
.\setup_venv.ps1
```

Manual setup:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

## Configuration

Runtime configuration is loaded from `.env` by `config.py`.

Important variables:

- `OPENAI_API_KEY`: optional API key placeholder for integrations.
- `INPUT_BOOKS_FOLDER`: source directory with books to scan.
- `OUTPUT_BOOKS_FOLDER`: target directory for the organized bookshelf.
- `TESSERACT_CMD`: full path to `tesseract.exe` (the installation folder is also accepted).
- `MODEL_NAME`: Ollama model. Thinking models (gemma4, qwen3, …) work with the default `LLM_THINK=false`; with thinking enabled, raise `LLM_NUM_PREDICT`.
- `RETRY_ERRORS`: retry books that failed in previous runs (default `true`).

All other tunables (page counts, OCR timeouts, LLM options, ZIP limits, logging) and their defaults are listed in `config.py`.

The repository ships with `.env.example` as a template. Your actual `.env` is ignored by git.

## Usage

Try a sample first, then run the whole library:

```powershell
python __main__.py --dry-run --limit 50     # nothing is copied, see results.dry-run.csv
python __main__.py --limit 50               # real run on the first 50 books
python __main__.py                          # everything
```

Other options: `--input` / `--output` override the folders from `.env`, `--only-ext pdf,djvu` limits formats, `--no-retry-errors` skips books that failed before. See `python __main__.py --help`.

Ctrl+C stops after the current page; unfinished books are picked up on the next run. Press it twice to exit immediately.

The run ends with a summary of statuses and the most frequent error reasons. Failed books are copied to `<OUTPUT>/Errors`, and every result is written to `results.csv` and `books.db`.

Run the tests:

```powershell
pip install -r requirements-dev.txt
python -m pytest
```

Optional maintenance:

```powershell
python truncate_db.py
```

## Repository Hygiene

- `.env` is excluded from version control.
- Logs, SQLite databases, caches, and `.venv` are ignored via `.gitignore`.
- `.env.example` documents the variables needed to run the project on another machine.

Note: commit metadata can still reveal the author email if it already exists in git history.

## License

This repository is licensed under GPL-2.0-only. See `LICENSE`.
