# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 The ScanBookShelf Authors

import csv
import difflib
import hashlib
import logging
import os
import re
import shutil

from config import ERRORS_SUBFOLDER, PATH_ALIAS_THRESHOLD
from normalization import normalize_for_match, token_key

logger = logging.getLogger("bookrouter")

MAX_NAME_LEN = 120
_WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def long_path(path: str) -> str:
    """Путь для файловых операций Windows: длиннее ~260 символов работает только с префиксом \\\\?\\."""
    if os.name != "nt" or not path:
        return path
    absolute = os.path.abspath(path)
    if absolute.startswith("\\\\?\\") or len(absolute) < 240:
        return path
    if absolute.startswith("\\\\"):
        return "\\\\?\\UNC\\" + absolute[2:]
    return "\\\\?\\" + absolute


def file_fingerprint(path: str, edge_bytes: int = 4 * 1024 * 1024) -> str:
    """Отпечаток для поиска дубликатов: размер + sha1 первых и последних 4 МБ.
    Полное хеширование тысяч книг заметно удлиняет прогон, а разные книги с совпадающими
    размером, началом и концом на практике не встречаются."""
    full_path = long_path(path)
    size = os.path.getsize(full_path)
    digest = hashlib.sha1()
    with open(full_path, "rb") as f:
        digest.update(f.read(edge_bytes))
        if size > edge_bytes:
            f.seek(max(edge_bytes, size - edge_bytes))
            digest.update(f.read(edge_bytes))
    return f"{size}:{digest.hexdigest()}"


def sanitize_filename(name: str, max_len: int = MAX_NAME_LEN) -> str:
    value = re.sub(r'[\\/*?:"<>|\x00-\x1f]', "-", str(name))
    value = re.sub(r"\s+", " ", value).strip()
    # Windows молча отбрасывает точки и пробелы в конце имени
    value = value.rstrip(". ")
    if len(value) > max_len:
        stem, ext = os.path.splitext(value)
        if len(ext) > 8:
            stem, ext = value, ""
        value = stem[: max_len - len(ext)].rstrip(". ") + ext
    if value.split(".", 1)[0].strip().upper() in _WINDOWS_RESERVED:
        value = f"_{value}"
    return value or "_"


def _sanitize_rel_path(raw_path: str) -> str:
    normalized = str(raw_path or "").replace("\\", "/").strip("/")
    parts = [part for part in normalized.split("/") if part and part not in (".", "..")]
    safe_parts = [sanitize_filename(part) for part in parts]
    return "/".join(part for part in safe_parts if part)


def _strip_errors_prefix(relative: str) -> str:
    """Вход может сам быть выходом прошлого прогона: не вкладываем Errors/file/Errors/file/…"""
    parts = relative.split("/")
    while len(parts) > 2 and parts[0].lower() == ERRORS_SUBFOLDER.lower() and parts[1].lower() in ("file", "zip"):
        parts = parts[2:]
    return "/".join(parts)


def append_csv(path: str, row):
    write_header = not os.path.exists(path)
    with open(path, mode="a", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, delimiter="|")
        if write_header:
            writer.writerow(
                [
                    "№",
                    "Файл",
                    "ISBN",
                    "Название",
                    "Автор",
                    "Серия",
                    "Категория",
                    "Источник",
                    "Уверенность",
                    "Статус",
                    "Причина ошибки",
                    "Тип источника",
                    "Путь архива",
                    "Элемент архива",
                    "Текст",
                    "Новый путь",
                ]
            )
        writer.writerow(row)


def _resolve_existing_subdir_name(parent_dir: str, candidate_name: str, threshold=PATH_ALIAS_THRESHOLD) -> str:
    if not os.path.isdir(long_path(parent_dir)):
        return candidate_name

    candidate_key = normalize_for_match(candidate_name)
    if not candidate_key:
        return candidate_name

    best_match = None
    best_ratio = 0.0
    try:
        for entry in os.scandir(long_path(parent_dir)):
            if not entry.is_dir():
                continue
            existing_key = normalize_for_match(entry.name)
            if not existing_key:
                continue
            if existing_key == candidate_key or token_key(entry.name) == token_key(candidate_name):
                return entry.name
            if min(len(existing_key), len(candidate_key)) < 4:
                continue
            ratio = difflib.SequenceMatcher(None, existing_key, candidate_key).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_match = entry.name
    except OSError:
        return candidate_name

    if best_match and best_ratio >= threshold:
        return best_match
    return candidate_name


def make_unique_destination(path: str) -> str:
    if not os.path.exists(long_path(path)):
        return path

    directory = os.path.dirname(path)
    filename = os.path.basename(path)
    stem, ext = os.path.splitext(filename)

    counter = 2
    while True:
        candidate = os.path.join(directory, f"{stem} ({counter}){ext}")
        if not os.path.exists(long_path(candidate)):
            return candidate
        counter += 1


def copy_to_errors(source, output_folder: str, reason: str, dry_run: bool = False) -> str:
    errors_root = os.path.join(output_folder, ERRORS_SUBFOLDER)
    origin_type = getattr(source, "origin_type", "file")
    source_root = getattr(source, "source_root", "")
    logical_path = getattr(source, "logical_path", "")
    display_name = getattr(source, "display_name", "unknown")
    archive_path = getattr(source, "archive_path", "")
    archive_member = getattr(source, "archive_member", "")
    materialized_path = getattr(source, "materialized_path", "")

    if origin_type == "zip":
        archive_base = sanitize_filename(os.path.splitext(os.path.basename(archive_path or "archive"))[0] or "archive")
        member_rel = _sanitize_rel_path(archive_member) or sanitize_filename(display_name)
        destination = os.path.join(errors_root, "zip", archive_base, *member_rel.split("/"))
    else:
        relative = ""
        if source_root and logical_path:
            try:
                relative = os.path.relpath(logical_path, source_root)
            except Exception:
                relative = os.path.basename(logical_path)
        relative = _sanitize_rel_path(relative or os.path.basename(logical_path or display_name))
        relative = _strip_errors_prefix(relative)
        destination = os.path.join(errors_root, "file", *relative.split("/"))

    src = ""
    if materialized_path and os.path.exists(long_path(materialized_path)):
        src = materialized_path
    elif origin_type == "file" and logical_path and os.path.exists(long_path(logical_path)):
        src = logical_path
    elif origin_type == "zip" and archive_path and os.path.exists(long_path(archive_path)):
        src = archive_path
        archive_base = sanitize_filename(os.path.splitext(os.path.basename(archive_path))[0] or "archive")
        destination = os.path.join(errors_root, "zip", archive_base, sanitize_filename(os.path.basename(archive_path)))

    if not src:
        logger.error("error_fallback_copy_failed reason=no_source_file origin=%s logical=%s", origin_type, logical_path)
        return ""

    if dry_run:
        logger.info("error_fallback_dry_run reason=%s source=%s destination=%s", reason, src, destination)
        return destination

    os.makedirs(long_path(os.path.dirname(destination)), exist_ok=True)
    if os.path.isfile(long_path(destination)) and os.path.getsize(long_path(destination)) == os.path.getsize(long_path(src)):
        # Повторная обработка той же книги: копия уже лежит в Errors
        logger.error(
            "error_fallback_reused reason=%s origin=%s source=%s destination=%s",
            reason,
            origin_type,
            src,
            destination,
        )
        return destination
    destination = make_unique_destination(destination)

    try:
        shutil.copy2(long_path(src), long_path(destination))
        logger.error(
            "error_fallback_copy reason=%s origin=%s source=%s destination=%s archive=%s member=%s",
            reason,
            origin_type,
            src,
            destination,
            archive_path,
            archive_member,
        )
        return destination
    except Exception as exc:
        logger.error(
            "error_fallback_copy_failed reason=%s origin=%s source=%s destination=%s error=%s",
            reason,
            origin_type,
            src,
            destination,
            exc,
        )
        return ""


def remove_stale_error_copy(old_path: str, output_folder: str):
    """После успешной переобработки удаляет прежнюю копию книги из Errors (только внутри OUTPUT/Errors)."""
    if not old_path or not output_folder:
        return
    errors_root = os.path.normcase(os.path.abspath(os.path.join(output_folder, ERRORS_SUBFOLDER)))
    candidate = os.path.normcase(os.path.abspath(old_path))
    if not candidate.startswith(errors_root + os.sep) or not os.path.isfile(long_path(old_path)):
        return
    try:
        os.remove(long_path(old_path))
        logger.info("error_copy_removed path=%s", old_path)
    except OSError as exc:
        logger.warning("error_copy_remove_failed path=%s error=%s", old_path, exc)


def copy_book_to_category(
    file_path: str,
    title: str,
    author: str,
    category: str,
    output_folder: str,
    dry_run: bool = False,
):
    char_for_splitting = "|" if ">" not in str(category) else ">"
    category_parts = [
        sanitize_filename(part.strip())
        for part in str(category).split(char_for_splitting)
        if part and part.strip()
    ]

    if not category_parts:
        category_parts = ["Другое"]

    category_path_parts = []
    current_parent = output_folder
    for index, part in enumerate(category_parts):
        # Taxonomy names are exact: punctuation distinguishes C++ from C#.
        # Only fiction author/series folders may reuse spelling variants.
        is_fiction_detail = category_parts[0] == "Художественные" and index >= 2
        resolved_part = _resolve_existing_subdir_name(current_parent, part) if is_fiction_detail else part
        category_path_parts.append(resolved_part)
        current_parent = os.path.join(current_parent, resolved_part)

    category_folder = os.path.join(output_folder, *category_path_parts)

    file_ext = os.path.splitext(file_path)[1]
    if category_path_parts == ["Другое"]:
        new_filename = sanitize_filename(os.path.basename(file_path))
    else:
        new_filename = sanitize_filename(f"{title} - {author}{file_ext}")
    destination = os.path.join(category_folder, new_filename)

    if dry_run:
        print(f"📂 Категория (dry-run): {' | '.join(category_path_parts)}")
        return destination

    try:
        os.makedirs(long_path(category_folder), exist_ok=True)
        destination = make_unique_destination(destination)
        shutil.copy2(long_path(file_path), long_path(destination))
        print(f"📂 Категория: {' | '.join(category_path_parts)}")
        return destination
    except Exception as e:
        print(f"⚠️ Ошибка при копировании: {e}")
        return None
