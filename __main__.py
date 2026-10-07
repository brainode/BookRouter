# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 The ScanBookShelf Authors

import argparse
import concurrent.futures
import logging
import os
import re
import signal
import sys
import time
from collections import Counter
from dataclasses import dataclass, field

from config import (
    EXTRACT_WORKERS,
    INPUT_BOOKS_FOLDER,
    MAX_PAGES,
    MAX_TAIL_PAGES,
    OUTPUT_BOOKS_FOLDER,
    RETRY_ERRORS,
)
from db import BookDB
from interrupt import Interrupted, stop_event
from llm import DEFAULT_CATEGORY, LLMUnavailableError, build_category_path, classify_category, extract_book_facts, warm_up_model
from logging_utils import install_print_logging, setup_logging
from metadata_enricher import MetadataEnricher
from normalization import extract_first_valid_isbn, is_unknown_label, normalize_isbn, parse_filename_hints
from preflight import run_preflight
from reader import OCRConfigError, extract_book, quality_score
from sources import BookSource, cleanup_source, collect_book_sources, materialize_zip_member
from utils import append_csv, copy_book_to_category, copy_to_errors, file_fingerprint, find_duplicate, long_path, remove_stale_error_copy, source_signature

logger = logging.getLogger("bookrouter")

CSV_TEXT_LIMIT = 300
# Столько подряд недоступных ответов Ollama — и прогон останавливается, чтобы не гонять всю библиотеку впустую
MAX_CONSECUTIVE_TRANSIENT = 3


@dataclass
class RunOptions:
    input_folder: str
    output_folder: str
    limit: int = 0
    dry_run: bool = False
    only_ext: set[str] = field(default_factory=set)
    retry_errors: bool = True
    review_only: bool = False

    @property
    def output_csv(self) -> str:
        return "results.dry-run.csv" if self.dry_run else "results.csv"


def parse_args(argv: list[str] | None = None) -> RunOptions:
    parser = argparse.ArgumentParser(
        prog="python __main__.py",
        description="Раскладывает книги из входной папки по категориям. Значения по умолчанию берутся из .env.",
    )
    parser.add_argument("--input", default=INPUT_BOOKS_FOLDER, help="входная папка (INPUT_BOOKS_FOLDER)")
    parser.add_argument("--output", default=OUTPUT_BOOKS_FOLDER, help="папка библиотеки (OUTPUT_BOOKS_FOLDER)")
    parser.add_argument("--limit", type=int, default=0, help="обработать не больше N источников")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="ничего не копировать и не записывать книги в БД; результат — в results.dry-run.csv",
    )
    parser.add_argument("--only-ext", default="", help="только эти форматы, например pdf,djvu")
    parser.add_argument("--review-only", action="store_true", help="повторить только книги, требующие ручной проверки")
    parser.add_argument(
        "--retry-errors",
        action=argparse.BooleanOptionalAction,
        default=RETRY_ERRORS,
        help="повторно обрабатывать книги с ошибками (RETRY_ERRORS)",
    )
    args = parser.parse_args(argv)
    only_ext = {
        "." + ext.strip().lower().lstrip(".")
        for ext in args.only_ext.split(",")
        if ext.strip()
    }
    return RunOptions(
        input_folder=args.input,
        output_folder=args.output,
        limit=max(0, args.limit),
        dry_run=args.dry_run,
        only_ext=only_ext,
        retry_errors=args.retry_errors,
        review_only=args.review_only,
    )


def signal_handler(sig, frame):
    if stop_event.is_set():
        print("\n⛔ Повторный сигнал прерывания — немедленный выход")
        os._exit(130)
    # Только выставляем флаг: потоки извлечения проверяют его между страницами,
    # главный цикл завершается сам и закрывает БД.
    print("\n⛔ Получен сигнал прерывания. Дожидаюсь текущих страниц и завершаю... (повторный Ctrl+C — выйти сразу)")
    stop_event.set()


def _normalized_origin_key(source: BookSource) -> tuple[str, str]:
    return str(source.origin_type or ""), str(source.logical_path or "").replace("/", "\\")


def _source_ext(source: BookSource) -> str:
    return os.path.splitext(source.display_name)[1].lower()


def _error_result(source: BookSource, index: int, status: str, reason: str, text: str = "") -> dict:
    return {
        "index": index,
        "file_name": source.display_name,
        "file_path": source.logical_path,
        "origin_type": source.origin_type,
        "origin_path": source.logical_path,
        "archive_path": source.archive_path,
        "archive_member": source.archive_member,
        "isbn": "",
        "isbn_norm": "",
        "title": "[ошибка]",
        "title_raw": "",
        "author": "",
        "author_raw": "",
        "series": "",
        "series_index": "",
        "category": "",
        "text": text,
        "new_path": "",
        "metadata_source": "error",
        "metadata_confidence": 0.0,
        "category_confidence": 0.0,
        "facts_confidence": 0.0,
        "provider_match_score": 0.0,
        "status": status,
        "error_reason": reason,
        "content_hash": "",
        "category_evidence": "",
        "category_source": "",
        "pub_year": "",
        "publisher": "",
        "edition": "",
        "book_format": "",
        "page_count": 0,
        "has_text_layer": 0,
        "quality": 0,
        "fb2_genres": "",
        "subjects": "",
    }


def _attach_error_fallback(result: dict, source: BookSource, options: RunOptions) -> dict:
    # Временные ошибки не копируем в Errors: книга будет обработана в следующем запуске
    if result.get("status") in ("ok", "needs_review", "interrupted", "duplicate", "error_transient"):
        return result

    reason = result.get("error_reason", "unknown_error")
    error_path = copy_to_errors(source, options.output_folder, reason, dry_run=options.dry_run)
    if error_path:
        result["new_path"] = error_path
    return result


def _duplicate_result(source: BookSource, index: int, content_hash: str, original_path: str) -> dict:
    result = _error_result(source, index, "duplicate", f"duplicate_of:{original_path}")
    result["title"] = ""
    result["metadata_source"] = "duplicate"
    result["new_path"] = original_path
    result["content_hash"] = content_hash
    return result


def extract_file_data(index: int, source: BookSource, known_hashes: dict[str, list[str]]):
    content_hash = ""
    source_size = source_mtime_ns = None

    fmt, page_count, ocr_used, embedded = "", 0, False, {}

    def _stage_result(status: str, error: str = "", isbn: str = "", text: str = "") -> dict:
        return {
            "fmt": fmt,
            "page_count": page_count,
            "ocr_used": ocr_used,
            "embedded": embedded,
            "status": status,
            "index": index,
            "source": source,
            "isbn": isbn,
            "text": text,
            "error": error,
            "content_hash": content_hash,
            "source_size": source_size,
            "source_mtime_ns": source_mtime_ns,
        }

    if stop_event.is_set():
        return _stage_result("interrupted", "processing_interrupted")

    print(f"📄 Извлечение {index}: {source.display_name}")

    try:
        source_size, source_mtime_ns = source_signature(source)
        if source.status_hint != "ready":
            return _stage_result(source.status_hint, source.error_reason or "source_not_ready")
        source = materialize_zip_member(source)
        io_path = source.materialized_path or source.logical_path
        content_hash = file_fingerprint(io_path)
        # Такое же содержимое уже разложено — извлечение и LLM не нужны
        duplicate_path = find_duplicate(io_path, content_hash, known_hashes)
        if duplicate_path:
            return _stage_result("duplicate", duplicate_path)
        extracted = extract_book(io_path, MAX_PAGES, MAX_TAIL_PAGES)
        fmt, page_count, ocr_used, embedded = extracted.fmt, extracted.page_count, extracted.ocr_used, extracted.embedded
        text_head, text_tail = extracted.head, extracted.tail
        if not (text_head.strip() or text_tail.strip()):
            return _stage_result("error_no_text", "no_text_extracted")
        isbn = extract_first_valid_isbn(f"{text_head}\n{text_tail}") or normalize_isbn(embedded.get("isbn")) or ""
        return _stage_result("ok", isbn=isbn, text=text_head or text_tail)
    except Interrupted:
        return _stage_result("interrupted", "processing_interrupted")
    except OCRConfigError as exc:
        # Проблема окружения, а не книги: продолжать бессмысленно, книга останется необработанной
        return _stage_result("fatal", str(exc))
    except Exception as exc:
        status = "error_archive" if source.origin_type == "zip" else "error_extract"
        return _stage_result(status, str(exc))


def _facts_from_filename(source: BookSource) -> dict | None:
    """Для книг без текста (сканы-картинки) — метаданные из имени файла, если оно осмысленное."""
    hints = parse_filename_hints(source.display_name)
    if is_unknown_label(hints["title"]) or is_unknown_label(hints["author"]):
        return None
    return {
        "title": hints["title"],
        "author": hints["author"],
        "isbn": "",
        "confidence": 0.3,
        "language_hint": "",
        "series_hint": hints.get("series", ""),
        "pub_year": "",
        "publisher": "",
        "edition": "",
    }


def process_file(extracted: dict, enricher: MetadataEnricher, options: RunOptions):
    source: BookSource = extracted["source"]
    index = extracted["index"]
    raw_isbn = extracted.get("isbn", "")
    text = extracted.get("text", "")
    status = extracted.get("status", "error_process")
    error_msg = extracted.get("error", "")
    content_hash = extracted.get("content_hash", "")

    print(f"🔍 Обработка {index}: {source.display_name}")

    def _failed(status_value: str, reason: str) -> dict:
        result = _error_result(source, index, status_value, reason, text=text)
        result["content_hash"] = content_hash
        return result

    if stop_event.is_set() or status == "interrupted":
        result = _failed("interrupted", "processing_interrupted")
        result["title"] = ""
        result["metadata_source"] = ""
        return result

    if status == "duplicate":
        return _duplicate_result(source, index, content_hash, error_msg)

    facts = None
    if status == "error_no_text":
        facts = _facts_from_filename(source)
        if facts:
            print(f"🖼️ Текста нет, метаданные из имени файла: {facts['title']} - {facts['author']}")

    if status != "ok" and facts is None:
        result = _failed(status, error_msg or "extract_stage_failed")
        result["metadata_source"] = "extract"
        return _attach_error_fallback(result, source, options)

    try:
        if facts is None:
            facts = extract_book_facts(text, filename=source.display_name, interrupted_flag=stop_event.is_set())
        if not facts.get("isbn") and raw_isbn:
            facts["isbn"] = raw_isbn
        embedded = extracted.get("embedded") or {}
        facts["pub_year"] = embedded.get("year") or facts.get("pub_year", "")
        facts["publisher"] = embedded.get("publisher") or facts.get("publisher", "")
        fmt = extracted.get("fmt", "")
        ocr_used = bool(extracted.get("ocr_used"))
        no_text = status == "error_no_text"

        enriched = enricher.enrich(facts)
        title_final = enriched.get("title", "") or "Неизвестное название"
        author_final = enriched.get("author", "") or "Неизвестный автор"
        series_final = enriched.get("series", "")
        isbn_final = enriched.get("isbn", "") or raw_isbn
        isbn_norm = normalize_isbn(isbn_final) or ""
        metadata_source = enriched.get("metadata_source", "local")
        if status == "error_no_text" and metadata_source == "local":
            metadata_source = "filename"

        category_data = classify_category(text, title_final, author_final, interrupted_flag=stop_event.is_set())
        category_base = category_data.get("category", "")
        if category_base.startswith("Художественные | ") and not series_final:
            series_final = "Без серии"
        category_path = build_category_path(category_base, author_final, series_final)

        # Прерывание во время запросов к LLM: метаданные заглушечные, книгу не копируем
        if stop_event.is_set():
            result = _failed("interrupted", "processing_interrupted")
            result["title"] = ""
            return result

        metadata_confidence = float(enriched.get("metadata_confidence", 0.0) or 0.0)
        result = {
            "index": index,
            "file_name": source.display_name,
            "file_path": source.logical_path,
            "origin_type": source.origin_type,
            "origin_path": source.logical_path,
            "archive_path": source.archive_path,
            "archive_member": source.archive_member,
            "isbn": isbn_final,
            "isbn_norm": isbn_norm,
            "title": title_final,
            "title_raw": facts.get("title", ""),
            "author": author_final,
            "author_raw": facts.get("author", ""),
            "series": series_final,
            "series_index": enriched.get("series_index", ""),
            "category": category_path,
            "text": text,
            "new_path": "",
            "metadata_source": metadata_source,
            "metadata_confidence": metadata_confidence,
            "category_confidence": float(category_data.get("confidence", 0.0) or 0.0),
            "facts_confidence": float(facts.get("confidence", 0.0) or 0.0),
            "provider_match_score": float(enriched.get("provider_match_score", 0.0) or 0.0),
            "status": "needs_review" if category_base == DEFAULT_CATEGORY else "ok",
            "error_reason": category_data.get("review_reason", "") if category_base == DEFAULT_CATEGORY else "",
            "content_hash": content_hash,
            "category_evidence": category_data.get("evidence", ""),
            "category_source": "llm",
            "pub_year": facts.get("pub_year", ""),
            "publisher": facts.get("publisher", ""),
            "edition": facts.get("edition", ""),
            "book_format": fmt,
            "page_count": extracted.get("page_count", 0),
            "has_text_layer": 0 if (no_text or ocr_used) else 1,
            "quality": 0 if no_text else quality_score(fmt, ocr_used),
            "fb2_genres": ",".join(embedded.get("genres", [])),
            "subjects": "; ".join(embedded.get("subjects", []))[:500],
        }

        io_path = source.materialized_path or source.logical_path
        new_path = copy_book_to_category(
            io_path, title_final, author_final, category_path, options.output_folder, dry_run=options.dry_run
        )
        if not new_path:
            result["status"] = "error_process"
            result["error_reason"] = "copy_to_category_failed"
            return _attach_error_fallback(result, source, options)

        print(f"📚 Книга: {title_final} - {author_final}")
        result["new_path"] = new_path
        return result
    except LLMUnavailableError as exc:
        return _failed("error_transient", str(exc))
    except Exception as exc:
        return _attach_error_fallback(_failed("error_process", str(exc)), source, options)


def _wait_result(future: concurrent.futures.Future):
    """Ждёт результат короткими интервалами, чтобы главный поток сразу обрабатывал Ctrl+C. None — прервано."""
    while True:
        try:
            return future.result(timeout=0.5)
        except concurrent.futures.TimeoutError:
            if stop_event.is_set():
                return None


def iter_extracted_files(sources: list[BookSource], known_hashes: dict[str, list[str]]):
    if not sources:
        return

    workers = max(1, min(EXTRACT_WORKERS, len(sources), (os.cpu_count() or 1)))
    prefetch = max(1, workers * 2)

    if workers == 1:
        for idx, source in enumerate(sources, start=1):
            if stop_event.is_set():
                break
            yield extract_file_data(idx, source, known_hashes)
        return

    print(f"⚙️ Предзагрузка извлечения текста: workers={workers}, prefetch={prefetch}")
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=workers)
    futures_by_index: dict[int, concurrent.futures.Future] = {}
    next_to_submit = 1
    try:
        while next_to_submit <= min(prefetch, len(sources)):
            source = sources[next_to_submit - 1]
            futures_by_index[next_to_submit] = pool.submit(extract_file_data, next_to_submit, source, known_hashes)
            next_to_submit += 1

        for expected_idx in range(1, len(sources) + 1):
            if stop_event.is_set():
                break

            future = futures_by_index.pop(expected_idx, None)
            if future is None:
                continue

            try:
                extracted = _wait_result(future)
            except Exception as exc:
                source = sources[expected_idx - 1]
                extracted = {
                    "status": "error_extract",
                    "index": expected_idx,
                    "source": source,
                    "isbn": "",
                    "text": "",
                    "error": str(exc),
                    "content_hash": "",
                    "fmt": "",
                    "page_count": 0,
                    "ocr_used": False,
                    "embedded": {},
                }

            if extracted is None:
                futures_by_index[expected_idx] = future
                break

            if next_to_submit <= len(sources):
                source = sources[next_to_submit - 1]
                futures_by_index[next_to_submit] = pool.submit(extract_file_data, next_to_submit, source, known_hashes)
                next_to_submit += 1

            yield extracted
    finally:
        # Незапущенные задачи отменяем, запущенные сами остановятся на ближайшей странице
        pool.shutdown(wait=True, cancel_futures=True)
        for future in futures_by_index.values():
            if future.done() and not future.cancelled() and future.exception() is None:
                cleanup_source(future.result()["source"])


def _store_result(db: BookDB, result: dict, options: RunOptions) -> int:
    origin_path = str(result["origin_path"]).replace("/", "\\")
    previous = db.find_book_by_origin(result["origin_type"], origin_path)
    book_id = db.upsert_book(
        time_added=time.strftime("%Y-%m-%d %H:%M:%S"),
        original_filename=result["file_name"],
        original_path=str(result["file_path"]).replace("/", "\\"),
        origin_type=result["origin_type"],
        origin_path=origin_path,
        archive_path=str(result["archive_path"]).replace("/", "\\"),
        archive_member=result["archive_member"],
        isbn=result["isbn"],
        isbn_norm=result["isbn_norm"],
        preview_text=result["text"],
        title=result["title"],
        title_raw=result["title_raw"],
        author=result["author"],
        author_raw=result["author_raw"],
        series=result["series"],
        series_index=result["series_index"],
        category=result["category"],
        metadata_source=result["metadata_source"],
        metadata_confidence=result["metadata_confidence"],
        category_confidence=result.get("category_confidence"),
        facts_confidence=result.get("facts_confidence"),
        source_size=result.get("source_size"),
        source_mtime_ns=result.get("source_mtime_ns"),
        provider_match_score=result["provider_match_score"],
        status=result["status"],
        error_reason=result["error_reason"],
        new_path=str(result["new_path"]).replace("/", "\\"),
        content_hash=result.get("content_hash") or None,
        category_evidence=result.get("category_evidence"),
        category_source=result.get("category_source"),
        pub_year=result.get("pub_year"),
        publisher=result.get("publisher"),
        edition=result.get("edition"),
        book_format=result.get("book_format"),
        page_count=result.get("page_count"),
        has_text_layer=result.get("has_text_layer"),
        quality=result.get("quality"),
        fb2_genres=result.get("fb2_genres"),
        subjects=result.get("subjects"),
    )
    if result["status"] in ("ok", "duplicate", "needs_review") and previous:
        old_path = previous.get("new_path") or ""
        if os.path.normcase(os.path.abspath(old_path)) != os.path.normcase(os.path.abspath(result["new_path"] or "")):
            remove_stale_error_copy(old_path, options.output_folder, include_review=previous["status"] == "needs_review")
    return book_id


def _csv_row(result: dict) -> list:
    text = " ".join(str(result["text"] or "").split())
    return [
        result["index"],
        result["file_name"],
        result["isbn"],
        result["title"],
        result["author"],
        result["series"],
        result["category"],
        result["metadata_source"],
        result["metadata_confidence"],
        result["status"],
        result["error_reason"],
        result["origin_type"],
        result["archive_path"],
        result["archive_member"],
        text[:CSV_TEXT_LIMIT],
        result["new_path"],
        result.get("category_confidence"),
        result.get("facts_confidence"),
    ]


def _reason_key(reason: str) -> str:
    """Группирует причины ошибок: убирает пути и числа, чтобы одинаковые ошибки считались вместе."""
    value = re.sub(r"'[^']*'|\"[^\"]*\"", "'…'", str(reason or ""))
    value = value.split("duplicate_of:", 1)[0] + ("duplicate_of" if "duplicate_of:" in value else "")
    value = re.sub(r"\d+", "N", value)
    return value[:100] or "—"


def _print_summary(status_counts: Counter, reason_counts: Counter):
    if not status_counts:
        return
    print("📊 Статусы: " + ", ".join(f"{status}={count}" for status, count in status_counts.most_common()))
    if reason_counts:
        print("📊 Частые причины ошибок:")
        for reason, count in reason_counts.most_common(10):
            print(f"   {count:>5} × {reason}")


def _select_sources(sources: list[BookSource], db: BookDB | None, options: RunOptions, runtime_logger) -> tuple[list[BookSource], int]:
    if options.only_ext:
        sources = [source for source in sources if _source_ext(source) in options.only_ext]

    skipped_existing = 0
    existing_origin_keys = db.get_all_origin_keys(only_ok=options.retry_errors) if db else set()
    source_states = db.get_source_states() if db else {}
    if options.review_only:
        review_keys = db.get_review_origin_keys() if db else set()
        sources = [source for source in sources if _normalized_origin_key(source) in review_keys]
        existing_origin_keys = set()
    if existing_origin_keys:
        filtered_sources: list[BookSource] = []
        for source in sources:
            key = _normalized_origin_key(source)
            state = source_states.get(key, {})
            unchanged = False
            if key in existing_origin_keys:
                try:
                    unchanged = source_signature(source) == (state.get("source_size"), state.get("source_mtime_ns"))
                except OSError:
                    pass
                if state.get("status") in ("ok", "duplicate", "needs_review"):
                    unchanged = unchanged and bool(state.get("new_path")) and os.path.isfile(long_path(state["new_path"]))
            if key in existing_origin_keys and unchanged:
                skipped_existing += 1
                runtime_logger.debug(
                    "skip_already_processed origin_type=%s origin_path=%s display=%s",
                    source.origin_type,
                    str(source.logical_path).replace("/", "\\"),
                    source.display_name,
                )
                continue
            filtered_sources.append(source)
        sources = filtered_sources

    if options.limit:
        sources = sources[: options.limit]
    return sources, skipped_existing


def main(argv: list[str] | None = None):
    options = parse_args(argv)
    runtime_logger = setup_logging()
    install_print_logging(runtime_logger)
    runtime_logger.debug("Application main() started options=%s", options)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, signal_handler)  # Ctrl+Break в консоли Windows

    sources, discovery_stats = collect_book_sources(options.input_folder, exclude_dirs=(options.output_folder,))
    extensions = {_source_ext(source) for source in sources}
    if options.only_ext:
        extensions &= options.only_ext
    problems = run_preflight(extensions, options.input_folder, options.output_folder, options.dry_run)
    if problems:
        print("⛔ Проверка окружения не пройдена, обработка не начата:")
        for problem in problems:
            print(f"   - {problem}")
        sys.exit(2)

    print("⏳ Загружаю модель в память...")
    try:
        print(f"✅ Модель готова за {warm_up_model():.1f}с")
    except LLMUnavailableError as exc:
        print(f"⛔ Ollama недоступна: {exc}")
        sys.exit(2)

    db = BookDB()
    enricher = MetadataEnricher(db=db)
    sources, skipped_existing = _select_sources(sources, db, options, runtime_logger)
    # Отпечатки уже разложенных книг; потоки извлечения только читают, главный поток дополняет
    known_hashes = db.get_ok_hashes()

    start_time = time.time()
    processed_ok = 0
    processed_errors = 0
    processed_review = 0
    processed_duplicates = 0
    consecutive_transient = 0
    status_counts: Counter = Counter()
    reason_counts: Counter = Counter()
    fatal_error = ""

    if options.dry_run:
        print("🧪 Режим dry-run: файлы не копируются, книги в БД не записываются")
    print(f"📚 Найдено {len(sources)} источников для обработки")
    if skipped_existing:
        print(f"⏭️ Пропущено уже обработанных: {skipped_existing}")

    extracted_iter = iter_extracted_files(sources, known_hashes)
    try:
        for extracted in extracted_iter:
            source: BookSource = extracted["source"]
            try:
                # Прерванные книги не сохраняем — следующий запуск обработает их заново
                if stop_event.is_set() or extracted["status"] == "interrupted":
                    break
                if extracted["status"] == "fatal":
                    fatal_error = extracted["error"]
                    stop_event.set()
                    break
                # Дубликат мог появиться уже после того, как поток проверил отпечаток
                content_hash = extracted.get("content_hash", "")
                if extracted["status"] != "duplicate" and content_hash in known_hashes:
                    duplicate_path = find_duplicate(source.materialized_path or source.logical_path, content_hash, known_hashes)
                    if duplicate_path:
                        extracted = {**extracted, "status": "duplicate", "error": duplicate_path}

                result = process_file(extracted, enricher, options)
                result["source_size"] = extracted.get("source_size")
                result["source_mtime_ns"] = extracted.get("source_mtime_ns")
                if result["status"] == "interrupted":
                    break

                if result["status"] == "error_transient":
                    consecutive_transient += 1
                    if consecutive_transient >= MAX_CONSECUTIVE_TRANSIENT:
                        fatal_error = f"{consecutive_transient} раза подряд: {result['error_reason']}"
                        stop_event.set()
                else:
                    consecutive_transient = 0

                book_id = None if options.dry_run else _store_result(db, result, options)
                if result["status"] == "ok" and content_hash:
                    candidates = known_hashes.setdefault(content_hash, [])
                    if result["new_path"] not in candidates:
                        candidates.append(result["new_path"])
                if options.dry_run:
                    append_csv(options.output_csv, _csv_row(result))

                status_counts[result["status"]] += 1
                if result["status"] == "ok":
                    processed_ok += 1
                elif result["status"] == "needs_review":
                    processed_review += 1
                elif result["status"] == "duplicate":
                    processed_duplicates += 1
                else:
                    processed_errors += 1
                    reason_counts[f"{result['status']}: {_reason_key(result['error_reason'])}"] += 1

                print(
                    f"✅ Сохранено: {result['file_name']} ({result['index']}/{len(sources)}) | "
                    f"{result['title']} | src={result['metadata_source']} | status={result['status']} | id={book_id}"
                )
            except Exception as exc:
                runtime_logger.exception("Ошибка сохранения результата index=%s error=%s", extracted.get("index"), exc)
                processed_errors += 1
                status_counts["error_persist"] += 1
                reason_counts["error_persist: " + _reason_key(str(exc))] += 1
                fatal_error = f"result_persistence_failed:{exc}"
                stop_event.set()
                break
            finally:
                cleanup_source(source)
    finally:
        extracted_iter.close()
        if not options.dry_run:
            try:
                db.export_results(options.output_csv)
            except Exception as exc:
                runtime_logger.exception("Ошибка экспорта CSV: %s", exc)
                fatal_error = fatal_error or f"csv_export_failed:{exc}"
        db.close()
        runtime_logger.debug("Database connection closed")

    if fatal_error:
        print(f"⛔ Ошибка выполнения: {fatal_error}")
        print("   Проверь журнал. Сохранённые записи остаются в БД; CSV можно восстановить командой export_results.py.")
    elif stop_event.is_set():
        print("⛔ Обработка прервана пользователем. Необработанные книги будут взяты при следующем запуске.")

    print(
        "Итог: ok={ok}, needs_review={review}, duplicate={duplicates}, errors={err}, skipped_existing={sk}, zip_processed={zp}, zip_skipped={zs}, regular={reg}, total={total}".format(
            ok=processed_ok,
            review=processed_review,
            duplicates=processed_duplicates,
            err=processed_errors,
            sk=skipped_existing,
            zp=discovery_stats.get("processed_zip_members", 0),
            zs=discovery_stats.get("skipped_zip_members", 0),
            reg=discovery_stats.get("regular_book_sources", 0),
            total=len(sources) + skipped_existing,
        )
    )
    _print_summary(status_counts, reason_counts)
    print(f"Обработано {processed_ok + processed_errors + processed_review + processed_duplicates} из {len(sources)} источников за {time.time() - start_time:.2f}с")
    print("✅ Программа завершена")
    if fatal_error:
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n⛔ Программа прервана пользователем")
    except Exception as exc:
        logging.getLogger("bookrouter").exception("Критическая ошибка: %s", exc)
        sys.exit(1)
