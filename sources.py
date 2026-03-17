# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 The ScanBookShelf Authors

import logging
import os
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath

from config import ARCHIVE_MAX_MEMBER_SIZE_MB, ARCHIVE_TEMP_ROOT, ARCHIVE_ZIP_ENABLED

logger = logging.getLogger("scanbookshelf")

SUPPORTED_BOOK_EXTENSIONS = {".pdf", ".djvu", ".djv", ".epub", ".fb2"}


@dataclass
class BookSource:
    display_name: str
    materialized_path: str
    logical_path: str
    origin_type: str
    archive_path: str = ""
    archive_member: str = ""
    cleanup_token: tempfile.TemporaryDirectory | None = None
    source_root: str = ""
    status_hint: str = "ready"
    error_reason: str = ""


def _is_supported_book_extension(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in SUPPORTED_BOOK_EXTENSIONS


def _is_suspicious_member_path(member: str) -> bool:
    normalized = member.replace("\\", "/").strip()
    if not normalized or normalized.startswith("/"):
        return True
    parts = [part for part in PurePosixPath(normalized).parts if part]
    return any(part == ".." for part in parts)


def _safe_temp_parent() -> str | None:
    custom_root = str(ARCHIVE_TEMP_ROOT or "").strip()
    if not custom_root:
        return None
    os.makedirs(custom_root, exist_ok=True)
    return custom_root


def _make_member_source(root: str, archive_path: str, member: zipfile.ZipInfo) -> BookSource:
    logical = f"zip://{archive_path}!{member.filename}"
    display_name = os.path.basename(member.filename) or member.filename
    return BookSource(
        display_name=display_name,
        materialized_path="",
        logical_path=logical,
        origin_type="zip",
        archive_path=archive_path,
        archive_member=member.filename,
        source_root=root,
    )


def _collect_zip_sources(root: str, archive_path: str, stats: dict[str, int]) -> list[BookSource]:
    sources: list[BookSource] = []
    logger.debug("zip_open archive=%s", archive_path)

    try:
        with zipfile.ZipFile(archive_path) as archive:
            members = sorted(archive.infolist(), key=lambda item: item.filename.lower())
            for member in members:
                if member.is_dir():
                    continue

                if not _is_supported_book_extension(member.filename):
                    continue

                source = _make_member_source(root, archive_path, member)
                logger.debug(
                    "zip_member_selected archive=%s member=%s size=%s",
                    archive_path,
                    member.filename,
                    member.file_size,
                )

                if _is_suspicious_member_path(member.filename):
                    source.status_hint = "error_archive"
                    source.error_reason = "suspicious_member_path"
                    stats["skipped_zip_members"] += 1
                    logger.warning(
                        "zip_member_skipped reason=%s archive=%s member=%s",
                        source.error_reason,
                        archive_path,
                        member.filename,
                    )
                    sources.append(source)
                    continue

                if member.flag_bits & 0x1:
                    source.status_hint = "error_archive"
                    source.error_reason = "encrypted_member"
                    stats["skipped_zip_members"] += 1
                    logger.warning(
                        "zip_member_skipped reason=%s archive=%s member=%s",
                        source.error_reason,
                        archive_path,
                        member.filename,
                    )
                    sources.append(source)
                    continue

                max_bytes = int(ARCHIVE_MAX_MEMBER_SIZE_MB) * 1024 * 1024
                if member.file_size > max_bytes:
                    source.status_hint = "error_archive"
                    source.error_reason = f"member_too_large>{ARCHIVE_MAX_MEMBER_SIZE_MB}mb"
                    stats["skipped_zip_members"] += 1
                    logger.warning(
                        "zip_member_skipped reason=%s archive=%s member=%s size=%s",
                        source.error_reason,
                        archive_path,
                        member.filename,
                        member.file_size,
                    )
                    sources.append(source)
                    continue

                stats["processed_zip_members"] += 1
                sources.append(source)
    except Exception as exc:
        stats["skipped_zip_members"] += 1
        logger.exception("zip_open_failed archive=%s error=%s", archive_path, exc)
        sources.append(
            BookSource(
                display_name=os.path.basename(archive_path),
                materialized_path="",
                logical_path=archive_path,
                origin_type="zip",
                archive_path=archive_path,
                source_root=root,
                status_hint="error_archive",
                error_reason=f"zip_open_failed:{exc}",
            )
        )

    return sources


def collect_book_sources(root: str) -> tuple[list[BookSource], dict[str, int]]:
    stats = {
        "processed_zip_members": 0,
        "skipped_zip_members": 0,
        "regular_book_sources": 0,
    }
    sources: list[BookSource] = []

    for current_root, _, files in os.walk(root):
        for name in sorted(files, key=lambda value: value.lower()):
            full_path = os.path.join(current_root, name)
            ext = os.path.splitext(name)[1].lower()

            if ext == ".zip" and ARCHIVE_ZIP_ENABLED:
                sources.extend(_collect_zip_sources(root, full_path, stats))
                continue

            if _is_supported_book_extension(name):
                stats["regular_book_sources"] += 1
                sources.append(
                    BookSource(
                        display_name=name,
                        materialized_path=full_path,
                        logical_path=full_path,
                        origin_type="file",
                        source_root=root,
                    )
                )

    return sources, stats


def materialize_zip_member(source: BookSource) -> BookSource:
    if source.origin_type != "zip":
        return source
    if source.status_hint != "ready":
        return source
    if source.materialized_path:
        return source

    temp_parent = _safe_temp_parent()
    temp_dir = tempfile.TemporaryDirectory(dir=temp_parent)
    member_name = os.path.basename(source.archive_member) or "member.bin"
    target_path = os.path.join(temp_dir.name, member_name)

    try:
        with zipfile.ZipFile(source.archive_path) as archive:
            with archive.open(source.archive_member) as src, open(target_path, "wb") as dst:
                shutil.copyfileobj(src, dst)
    except Exception as exc:
        temp_dir.cleanup()
        raise RuntimeError(f"zip_materialize_failed:{exc}") from exc

    source.materialized_path = target_path
    source.cleanup_token = temp_dir
    logger.debug(
        "zip_member_processed archive=%s member=%s materialized=%s",
        source.archive_path,
        source.archive_member,
        target_path,
    )
    return source


def cleanup_source(source: BookSource):
    token = source.cleanup_token
    if not token:
        return
    try:
        token.cleanup()
        logger.debug(
            "zip_cleanup archive=%s member=%s",
            source.archive_path,
            source.archive_member,
        )
    except Exception as exc:
        logger.warning("zip_cleanup_failed archive=%s member=%s error=%s", source.archive_path, source.archive_member, exc)
    finally:
        source.cleanup_token = None
