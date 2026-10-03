# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 The ScanBookShelf Authors

from __future__ import annotations

import os
from pathlib import Path


def _load_dotenv(dotenv_path: Path) -> None:
    if not dotenv_path.exists():
        return

    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue

        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]

        os.environ.setdefault(key, value)


def _get_str(name: str, default: str) -> str:
    return os.getenv(name, default)


def _get_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _get_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _get_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


_load_dotenv(Path(__file__).resolve().parent / ".env")

OPENAI_API_KEY = _get_str("OPENAI_API_KEY", "")
MODEL_NAME = _get_str("MODEL_NAME", "gemma3:12b")
LLM_TEMPERATURE = _get_float("LLM_TEMPERATURE", 0.15)
LLM_NUM_CTX = _get_int("LLM_NUM_CTX", 8192)
LLM_NUM_PREDICT = _get_int("LLM_NUM_PREDICT", 220)
LLM_KEEP_ALIVE = _get_str("LLM_KEEP_ALIVE", "20m")
LLM_THINK = _get_bool("LLM_THINK", False)
LLM_TIMEOUT_SEC = _get_float("LLM_TIMEOUT_SEC", 300.0)
LLM_REQUEST_DELAY_SEC = _get_float("LLM_REQUEST_DELAY_SEC", 0.0)
EXTRACT_WORKERS = _get_int("EXTRACT_WORKERS", 2)
PATH_ALIAS_THRESHOLD = _get_float("PATH_ALIAS_THRESHOLD", 0.88)
ENRICH_ENABLED = _get_bool("ENRICH_ENABLED", True)
ENRICH_HTTP_TIMEOUT = _get_int("ENRICH_HTTP_TIMEOUT", 8)
ENRICH_RETRIES = _get_int("ENRICH_RETRIES", 2)
ENRICH_CACHE_TTL_DAYS = _get_int("ENRICH_CACHE_TTL_DAYS", 90)
ENRICH_MIN_MATCH_SCORE = _get_float("ENRICH_MIN_MATCH_SCORE", 0.82)
ENRICH_PROVIDERS = _get_str("ENRICH_PROVIDERS", "openlibrary,googlebooks")
LOG_FILE = _get_str("LOG_FILE", "app.log")
LOG_LEVEL = _get_str("LOG_LEVEL", "DEBUG")
LOG_TO_CONSOLE = _get_bool("LOG_TO_CONSOLE", True)
ARCHIVE_ZIP_ENABLED = _get_bool("ARCHIVE_ZIP_ENABLED", True)
ARCHIVE_TEMP_ROOT = _get_str("ARCHIVE_TEMP_ROOT", "")
ARCHIVE_MAX_MEMBER_SIZE_MB = _get_int("ARCHIVE_MAX_MEMBER_SIZE_MB", 250)
ERRORS_SUBFOLDER = _get_str("ERRORS_SUBFOLDER", "Errors")
RETRY_ERRORS = _get_bool("RETRY_ERRORS", True)

MAX_PAGES = _get_int("MAX_PAGES", 8)
MAX_TAIL_PAGES = _get_int("MAX_TAIL_PAGES", 4)
WORDS_PER_PAGES = _get_int("WORDS_PER_PAGES", 300)
LANGUAGES = _get_str("LANGUAGES", "eng+rus")
OCR_ENABLED = _get_bool("OCR_ENABLED", True)
OCR_PAGE_TIMEOUT_SEC = _get_int("OCR_PAGE_TIMEOUT_SEC", 45)
# Разрешение рендера страниц для OCR: выше 300 dpi Tesseract резко замедляется без выигрыша в качестве
OCR_DPI = _get_int("OCR_DPI", 300)
DDJVU_PAGE_TIMEOUT_SEC = _get_int("DDJVU_PAGE_TIMEOUT_SEC", 90)

INPUT_BOOKS_FOLDER = _get_str("INPUT_BOOKS_FOLDER", "")
OUTPUT_BOOKS_FOLDER = _get_str("OUTPUT_BOOKS_FOLDER", "")
TESSERACT_CMD = _get_str("TESSERACT_CMD", "")

CATEGORY_TREE = """
Уровень 1 | Уровень 2 | Уровень 3
Требует внимания
Художественные | Классика
Художественные | Детская
Художественные | Фэнтези
Художественные | Научная фантастика
Художественные | Драма
Художественные | Детектив
Художественные | Ужасы
Художественные | Другое
История
IT | Компьютерная графика и моделирование
IT | Администрирование
IT | AI и ML
IT | Алгоритмы и структуры данных
IT | Основы информатики
IT | Data Science
IT | DevOps
IT | Разработка игр
IT | Информационная безопасность
IT | Разработка ПО | Qt
IT | Веб-разработка | JavaScript и TypeScript
IT | Базы данных | SQL
IT | Базы данных | Общее
IT | Языки программирования | C++
IT | Языки программирования | Rust
IT | Языки программирования | Go
IT | Языки программирования | Python
IT | Языки программирования | C#
IT | Языки программирования | Asm
Иностранные языки | English
Иностранные языки | Spanish
Саморазвитие
Психология
Наука | Математика | Геометрия
Наука | Математика | Статистика
Наука | Математика | Теория вероятностей
Наука | Математика | Математический анализ
Наука | Математика | Топология
Наука | Математика | Логика
Наука | Математика | Теория графов
Наука | Космос
Наука | Физика
"""
