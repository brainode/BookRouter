# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 The ScanBookShelf Authors

import csv
import difflib
import logging
import os
import re
import shutil

from config import ERRORS_SUBFOLDER, PATH_ALIAS_THRESHOLD
from normalization import normalize_for_match

logger = logging.getLogger("scanbookshelf")


def sanitize_filename(name: str) -> str:
    return re.sub(r'[\\/*?:"<>|]', "-", str(name))


def _sanitize_rel_path(raw_path: str) -> str:
    normalized = str(raw_path or "").replace("\\", "/").strip("/")
    parts = [part for part in normalized.split("/") if part and part not in (".", "..")]
    safe_parts = [sanitize_filename(part) for part in parts]
    return "/".join(part for part in safe_parts if part)


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
    if not os.path.isdir(parent_dir):
        return candidate_name

    candidate_key = normalize_for_match(candidate_name)
    if not candidate_key:
        return candidate_name

    best_match = None
    best_ratio = 0.0
    try:
        for entry in os.scandir(parent_dir):
            if not entry.is_dir():
                continue
            existing_key = normalize_for_match(entry.name)
            if not existing_key:
                continue
            if existing_key == candidate_key:
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
    if not os.path.exists(path):
        return path

    directory = os.path.dirname(path)
    filename = os.path.basename(path)
    stem, ext = os.path.splitext(filename)

    counter = 2
    while True:
        candidate = os.path.join(directory, f"{stem} ({counter}){ext}")
        if not os.path.exists(candidate):
            return candidate
        counter += 1


def copy_to_errors(source, output_folder: str, reason: str) -> str:
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
        destination = os.path.join(errors_root, "file", *relative.split("/"))

    src = ""
    if materialized_path and os.path.exists(materialized_path):
        src = materialized_path
    elif origin_type == "file" and logical_path and os.path.exists(logical_path):
        src = logical_path
    elif origin_type == "zip" and archive_path and os.path.exists(archive_path):
        src = archive_path
        archive_base = sanitize_filename(os.path.splitext(os.path.basename(archive_path))[0] or "archive")
        destination = os.path.join(errors_root, "zip", archive_base, sanitize_filename(os.path.basename(archive_path)))

    if not src:
        logger.error("error_fallback_copy_failed reason=no_source_file origin=%s logical=%s", origin_type, logical_path)
        return ""

    os.makedirs(os.path.dirname(destination), exist_ok=True)
    destination = make_unique_destination(destination)

    try:
        shutil.copy2(src, destination)
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


def copy_book_to_category(file_path: str, title: str, author: str, category: str, output_folder: str):
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
    for part in category_parts:
        resolved_part = _resolve_existing_subdir_name(current_parent, part)
        category_path_parts.append(resolved_part)
        current_parent = os.path.join(current_parent, resolved_part)

    category_folder = os.path.join(output_folder, *category_path_parts)
    os.makedirs(category_folder, exist_ok=True)

    file_ext = os.path.splitext(file_path)[1]
    if category_path_parts == ["Другое"]:
        new_filename = sanitize_filename(os.path.basename(file_path))
    else:
        new_filename = sanitize_filename(f"{title} - {author}{file_ext}")
    destination = os.path.join(category_folder, new_filename)
    destination = make_unique_destination(destination)

    try:
        shutil.copy2(file_path, destination)
        print(f"📂 Категория: {' | '.join(category_path_parts)}")
        return destination
    except Exception as e:
        print(f"⚠️ Ошибка при копировании: {e}")
        return None
