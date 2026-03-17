# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 The ScanBookShelf Authors

import concurrent.futures
import logging
import os
import signal
import sys
import time

from config import EXTRACT_WORKERS, INPUT_BOOKS_FOLDER, MAX_PAGES, MAX_TAIL_PAGES, OUTPUT_BOOKS_FOLDER
from db import BookDB
from llm import build_category_path, classify_category, extract_book_facts
from logging_utils import install_print_logging, setup_logging
from metadata_enricher import MetadataEnricher
from normalization import extract_first_valid_isbn, normalize_isbn
from reader import extract_text_with_ends
from sources import BookSource, cleanup_source, collect_book_sources, materialize_zip_member
from utils import append_csv, copy_book_to_category, copy_to_errors

interrupted = False
executor = None
logger = logging.getLogger("scanbookshelf")


def signal_handler(sig, frame):
    global interrupted, executor
    print("\n⛔ Получен сигнал прерывания. Завершение...")
    interrupted = True
    if executor:
        executor.shutdown(wait=False)
    sys.exit(0)


def _normalized_origin_key(source: BookSource) -> tuple[str, str]:
    return str(source.origin_type or ""), str(source.logical_path or "").replace("/", "\\")


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
        "provider_match_score": 0.0,
        "status": status,
        "error_reason": reason,
    }


def _attach_error_fallback(result: dict, source: BookSource) -> dict:
    if result.get("status") == "ok":
        return result
    if result.get("status") == "interrupted":
        return result

    reason = result.get("error_reason", "unknown_error")
    error_path = copy_to_errors(source, OUTPUT_BOOKS_FOLDER, reason)
    if error_path:
        result["new_path"] = error_path
    return result


def extract_file_data(index: int, source: BookSource):
    print(f"📄 Извлечение {index}: {source.display_name}")

    if interrupted:
        return {
            "status": "interrupted",
            "index": index,
            "source": source,
            "isbn": "",
            "text": "",
            "error": "",
        }

    if source.status_hint != "ready":
        reason = source.error_reason or "source_not_ready"
        return {
            "status": source.status_hint,
            "index": index,
            "source": source,
            "isbn": "",
            "text": "",
            "error": reason,
        }

    try:
        source = materialize_zip_member(source)
        io_path = source.materialized_path or source.logical_path
        text_head, text_tail = extract_text_with_ends(io_path, MAX_PAGES, MAX_TAIL_PAGES)
        isbn = extract_first_valid_isbn(f"{text_head}\n{text_tail}") or ""
        return {
            "status": "ok",
            "index": index,
            "source": source,
            "isbn": isbn,
            "text": text_head,
            "error": "",
        }
    except Exception as exc:
        status = "error_archive" if source.origin_type == "zip" else "error_extract"
        return {
            "status": status,
            "index": index,
            "source": source,
            "isbn": "",
            "text": "",
            "error": str(exc),
        }


def process_file(extracted: dict, enricher: MetadataEnricher):
    source: BookSource = extracted["source"]
    index = extracted["index"]
    raw_isbn = extracted.get("isbn", "")
    text = extracted.get("text", "")
    status = extracted.get("status", "error_process")
    error_msg = extracted.get("error", "")

    print(f"🔍 Обработка {index}: {source.display_name}")

    if interrupted or status == "interrupted":
        result = _error_result(source, index, "interrupted", "processing_interrupted", text=text)
        result["title"] = ""
        result["metadata_source"] = ""
        return result

    if status != "ok":
        result = _error_result(source, index, status, error_msg or "extract_stage_failed", text=text)
        result["metadata_source"] = "extract"
        return _attach_error_fallback(result, source)

    try:
        facts = extract_book_facts(text, filename=source.display_name, interrupted_flag=interrupted)
        if not facts.get("isbn") and raw_isbn:
            facts["isbn"] = raw_isbn

        enriched = enricher.enrich(facts)
        title_final = enriched.get("title", "") or "Неизвестное название"
        author_final = enriched.get("author", "") or "Неизвестный автор"
        series_final = enriched.get("series", "")
        isbn_final = enriched.get("isbn", "") or raw_isbn
        isbn_norm = normalize_isbn(isbn_final) or ""

        category_data = classify_category(text, title_final, author_final, interrupted_flag=interrupted)
        category_base = category_data.get("category", "")
        if category_base.startswith("Художественные | ") and not series_final:
            series_final = "Без серии"
        category_path = build_category_path(category_base, author_final, series_final)

        io_path = source.materialized_path or source.logical_path
        new_path = copy_book_to_category(io_path, title_final, author_final, category_path, OUTPUT_BOOKS_FOLDER)
        if new_path:
            print(f"📚 Книга: {title_final} - {author_final}")
        else:
            error_result = _error_result(source, index, "error_process", "copy_to_category_failed", text=text)
            error_result["title"] = title_final
            error_result["author"] = author_final
            error_result["isbn"] = isbn_final
            error_result["isbn_norm"] = isbn_norm
            error_result["title_raw"] = facts.get("title", "")
            error_result["author_raw"] = facts.get("author", "")
            error_result["series"] = series_final
            error_result["series_index"] = enriched.get("series_index", "")
            error_result["category"] = category_path
            error_result["metadata_source"] = enriched.get("metadata_source", "local")
            error_result["metadata_confidence"] = max(
                float(enriched.get("metadata_confidence", 0.0) or 0.0),
                float(facts.get("confidence", 0.0) or 0.0),
                float(category_data.get("confidence", 0.0) or 0.0),
            )
            error_result["provider_match_score"] = float(enriched.get("provider_match_score", 0.0) or 0.0)
            return _attach_error_fallback(error_result, source)

        metadata_confidence = max(
            float(enriched.get("metadata_confidence", 0.0) or 0.0),
            float(facts.get("confidence", 0.0) or 0.0),
            float(category_data.get("confidence", 0.0) or 0.0),
        )

        return {
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
            "new_path": new_path or "",
            "metadata_source": enriched.get("metadata_source", "local"),
            "metadata_confidence": metadata_confidence,
            "provider_match_score": float(enriched.get("provider_match_score", 0.0) or 0.0),
            "status": "ok",
            "error_reason": "",
        }
    except Exception as exc:
        result = _error_result(source, index, "error_process", str(exc), text=text)
        return _attach_error_fallback(result, source)


def iter_extracted_files(sources: list[BookSource]):
    global executor
    if not sources:
        return

    workers = max(1, min(EXTRACT_WORKERS, len(sources), (os.cpu_count() or 1)))
    prefetch = max(1, workers * 2)

    if workers == 1:
        for idx, source in enumerate(sources, start=1):
            if interrupted:
                break
            yield extract_file_data(idx, source)
        return

    print(f"⚙️ Предзагрузка извлечения текста: workers={workers}, prefetch={prefetch}")
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        executor = pool
        futures_by_index: dict[int, concurrent.futures.Future] = {}
        next_to_submit = 1

        while next_to_submit <= min(prefetch, len(sources)):
            source = sources[next_to_submit - 1]
            futures_by_index[next_to_submit] = pool.submit(extract_file_data, next_to_submit, source)
            next_to_submit += 1

        for expected_idx in range(1, len(sources) + 1):
            if interrupted:
                break

            future = futures_by_index.pop(expected_idx, None)
            if future is None:
                continue

            try:
                extracted = future.result()
            except Exception as exc:
                source = sources[expected_idx - 1]
                extracted = {
                    "status": "error_extract",
                    "index": expected_idx,
                    "source": source,
                    "isbn": "",
                    "text": "",
                    "error": str(exc),
                }

            if next_to_submit <= len(sources):
                source = sources[next_to_submit - 1]
                futures_by_index[next_to_submit] = pool.submit(extract_file_data, next_to_submit, source)
                next_to_submit += 1

            yield extracted
    executor = None


def main():
    global interrupted

    runtime_logger = setup_logging()
    install_print_logging(runtime_logger)
    runtime_logger.debug("Application main() started")

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    db = BookDB()
    enricher = MetadataEnricher(db=db)

    output_csv = "results.csv"
    sources, discovery_stats = collect_book_sources(INPUT_BOOKS_FOLDER)
    existing_origin_keys = db.get_all_origin_keys()
    skipped_existing = 0
    if existing_origin_keys:
        filtered_sources: list[BookSource] = []
        for source in sources:
            if _normalized_origin_key(source) in existing_origin_keys:
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

    start_time = time.time()
    processed_ok = 0
    processed_errors = 0

    print(f"📚 Найдено {len(sources)} источников для обработки")
    if skipped_existing:
        print(f"⏭️ Пропущено уже обработанных: {skipped_existing}")

    try:
        for extracted in iter_extracted_files(sources):
            source: BookSource = extracted["source"]
            try:
                if interrupted:
                    break

                result = process_file(extracted, enricher)
                book_id = db.insert_book(
                    time_added=time.strftime("%Y-%m-%d %H:%M:%S"),
                    original_filename=result["file_name"],
                    original_path=str(result["file_path"]).replace("/", "\\"),
                    origin_type=result["origin_type"],
                    origin_path=str(result["origin_path"]).replace("/", "\\"),
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
                    provider_match_score=result["provider_match_score"],
                    status=result["status"],
                    error_reason=result["error_reason"],
                    new_path=str(result["new_path"]).replace("/", "\\"),
                )

                append_csv(
                    output_csv,
                    [
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
                        result["text"],
                        result["new_path"],
                    ],
                )

                if result["status"] == "ok":
                    processed_ok += 1
                else:
                    processed_errors += 1

                print(
                    f"✅ Сохранено: {result['file_name']} ({result['index']}/{len(sources)}) | "
                    f"{result['title']} | src={result['metadata_source']} | status={result['status']} | id={book_id}"
                )
            except Exception as exc:
                runtime_logger.exception("Ошибка сохранения результата index=%s error=%s", extracted.get("index"), exc)
            finally:
                cleanup_source(source)
    finally:
        db.close()
        runtime_logger.debug("Database connection closed")

    print(
        "Итог: ok={ok}, errors={err}, skipped_existing={sk}, zip_processed={zp}, zip_skipped={zs}, regular={reg}, total={total}".format(
            ok=processed_ok,
            err=processed_errors,
            sk=skipped_existing,
            zp=discovery_stats.get("processed_zip_members", 0),
            zs=discovery_stats.get("skipped_zip_members", 0),
            reg=discovery_stats.get("regular_book_sources", 0),
            total=len(sources) + skipped_existing,
        )
    )
    print(f"Обработано {processed_ok + processed_errors} из {len(sources)} источников за {time.time() - start_time:.2f}с")
    print("✅ Программа завершена")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n⛔ Программа прервана пользователем")
    except Exception as exc:
        logging.getLogger("scanbookshelf").exception("Критическая ошибка: %s", exc)
