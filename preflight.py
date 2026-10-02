# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 The ScanBookShelf Authors

import os
import shutil
import tempfile

import ollama
import pytesseract

from config import LANGUAGES, MODEL_NAME, OCR_ENABLED
from reader import TESSERACT_PATH

DJVU_TOOLS = ("djvused", "djvutxt", "ddjvu")


def check_folders(input_folder: str, output_folder: str, dry_run: bool = False) -> list[str]:
    # Папка вывода внутри входной допустима: collect_book_sources её не сканирует
    problems = []
    if not input_folder or not os.path.isdir(input_folder):
        problems.append(f"Входная папка не существует: {input_folder!r} (INPUT_BOOKS_FOLDER или --input)")
    if not output_folder:
        problems.append("Папка вывода не задана (OUTPUT_BOOKS_FOLDER или --output)")
        return problems
    if dry_run:
        return problems
    try:
        os.makedirs(output_folder, exist_ok=True)
        with tempfile.TemporaryFile(dir=output_folder):
            pass
    except OSError as exc:
        problems.append(f"Папка вывода недоступна на запись: {exc}")
    return problems


def check_tesseract() -> list[str]:
    if not OCR_ENABLED:
        return []
    if TESSERACT_PATH and not os.path.isfile(TESSERACT_PATH):
        return [f"TESSERACT_CMD не найден: {TESSERACT_PATH!r} (нужен путь к tesseract.exe)"]
    try:
        pytesseract.get_tesseract_version()
        installed = set(pytesseract.get_languages(config=""))
    except Exception as exc:
        return [f"Tesseract не запускается ({TESSERACT_PATH or 'tesseract из PATH'}): {exc}"]
    missing = [lang for lang in LANGUAGES.split("+") if lang and lang not in installed]
    if missing:
        return [f"В Tesseract нет языков {missing} из LANGUAGES={LANGUAGES!r}; установлены: {sorted(installed)}"]
    return []


def check_djvu_tools() -> list[str]:
    missing = [tool for tool in DJVU_TOOLS if not shutil.which(tool)]
    if missing:
        return [f"Для djvu нужны утилиты DjVuLibre в PATH, не найдены: {', '.join(missing)}"]
    return []


def _model_names(response) -> set[str]:
    # ollama>=0.4 возвращает ListResponse с .models[].model, старые версии — dict с models[].name
    models = getattr(response, "models", None)
    if models is None and isinstance(response, dict):
        models = response.get("models", [])
    names = set()
    for item in models or []:
        name = getattr(item, "model", None) or (item.get("model") or item.get("name") if isinstance(item, dict) else None)
        if name:
            names.add(name)
    return names


def check_ollama() -> list[str]:
    try:
        names = _model_names(ollama.list())
    except Exception as exc:
        return [f"Ollama недоступна: {exc}. Запусти Ollama и повтори"]
    wanted = MODEL_NAME if ":" in MODEL_NAME else f"{MODEL_NAME}:latest"
    if wanted not in names:
        return [f"Модели {MODEL_NAME!r} нет в Ollama (ollama pull {MODEL_NAME}); доступны: {sorted(names)}"]
    return []


def run_preflight(extensions: set[str], input_folder: str, output_folder: str, dry_run: bool = False) -> list[str]:
    """Проверяет окружение до обработки. extensions — расширения найденных источников (например {'.pdf'})."""
    problems = check_folders(input_folder, output_folder, dry_run) + check_ollama()
    if extensions & {".pdf", ".djvu", ".djv"}:
        problems += check_tesseract()
    if extensions & {".djvu", ".djv"}:
        problems += check_djvu_tools()
    return problems
