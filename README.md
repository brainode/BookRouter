# BookRouter

![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)
![AI Assisted](https://img.shields.io/badge/AI-Assisted-0A7E8C)

BookRouter scans a folder of books, extracts text from supported formats, enriches metadata with AI and public catalog providers, and copies the books into a categorized output bookshelf.

## Features

- Scans local folders for `pdf`, `djvu`, `djv`, `epub`, `fb2`, and ZIP-contained books.
- Extracts text with OCR fallback for image-based documents.
- Enriches title, author, ISBN, and series using local heuristics plus OpenLibrary and Google Books.
- Persists processing state in SQLite to avoid reprocessing the same sources.
- Copies processed books into category-based folders.

## Requirements

- Python 3.11 or newer.
- Tesseract OCR installed locally if OCR is enabled.
- DjVuLibre tools in `PATH` if you process `djvu` or `djv` files.
- A local Ollama model if you use the default `MODEL_NAME=gemma3:12b`.

The project imports these Python packages:

- `beautifulsoup4`
- `ebooklib`
- `numpy`
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
- `TESSERACT_CMD`: full path to `tesseract.exe` when OCR is enabled.

The repository ships with `.env.example` as a template. Your actual `.env` is ignored by git.

## Usage

Run the main pipeline:

```powershell
python __main__.py
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
