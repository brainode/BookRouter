# Codemap: конвейер сортировки

Поток одной книги: `sources.collect_book_sources` → `__main__._select_sources` → `__main__.iter_extracted_files` (потоки) → `__main__.extract_file_data` → `__main__.process_file` → `__main__._store_result`.

## `__main__.py`

| Символ | Что делает |
|---|---|
| `RunOptions`, `parse_args` | CLI: `--input --output --limit --dry-run --only-ext --review-only --[no-]retry-errors` |
| `signal_handler` | только `stop_event.set()`; второй сигнал → `os._exit(130)` |
| `_normalized_origin_key` | ключ `(origin_type, path с \\)` для сравнения с БД |
| `_error_result` | **эталонная форма словаря результата** — все ключи, которые ждут `_store_result` и `_csv_row` |
| `_attach_error_fallback` | копия в `<OUTPUT>/Errors` для ошибочных статусов |
| `_duplicate_result` | результат `duplicate` |
| `extract_file_data` | в потоке: подпись источника, распаковка zip, отпечаток, поиск побайтового дубля, `reader.extract_text_with_ends`, ISBN из текста. Возвращает stage-dict (`status`, `text`, `isbn`, `content_hash`, …) |
| `_facts_from_filename` | факты из имени файла для книг без текста |
| `process_file` | главный этап: `extract_book_facts` → `enricher.enrich` → `authors.resolve_author(db, …)` (slug папки автора, `author_id`; `db` передаёт `main`) → `classify_category` → `build_category_path` → `copy_book_to_category`. Возвращает result-dict |
| `iter_extracted_files` | упорядоченная предвыборка извлечения в `ThreadPoolExecutor` |
| `_store_result` | `db.upsert_book` + удаление устаревшей копии из `Errors`/`Требует внимания` |
| `_csv_row`, `_reason_key`, `_print_summary` | CSV dry-run и итоговая сводка |
| `_select_sources` | фильтр `--only-ext`, пропуск уже обработанных неизменённых источников, `--limit` |
| `main` | preflight → прогрев модели → цикл → экспорт CSV |

Статусы: `ok`, `needs_review`, `duplicate`, `error_extract`, `error_archive`, `error_no_text`, `error_process`, `error_transient`, `interrupted` (не сохраняется), `fatal` (стоп прогона). После T50 добавится `same_edition`.

## `sources.py`
`BookSource` (dataclass: `display_name`, `materialized_path`, `logical_path`, `origin_type` file|zip, `archive_path`, `archive_member`, `source_root`, `status_hint`, `error_reason`). `collect_book_sources(root, exclude_dirs)` → `(sources, stats)`. `materialize_zip_member`, `cleanup_source`. Расширения — `SUPPORTED_BOOK_EXTENSIONS`.

## `reader.py`
- `detect_format(path)` — по сигнатуре: `pdf|djvu|epub|fb2`, иначе `ExtractError("unknown_format:…")`.
- `extract_text_with_ends(path, head_pages, tail_pages) -> (head, tail)` — диспетчер по формату.
- PDF: `extract_text_pdf`, `_pdf_pages_text`, `perform_ocr_on_page`. DjVu: `extract_text_djvu`, `_djvu_pages_text`, `_run_tool`, `prepare_book_path` (ASCII-копия). EPUB: `extract_text_epub`, `_epub_spine_paths`, `_epub_words`, `_pymupdf_text`. FB2: `extract_text_fb2`, `_read_fb2_bytes`.
- OCR: `ocr_image`, `_ocr_pages`, `needs_ocr`, `_tail_ocr_needed`.
- Исключения: `OCRConfigError` (окружение → fatal), `OCRTimeoutError`, `ExtractError` (книга → error_extract).
- `ExtractedText` (head, tail, fmt, page_count, ocr_used, embedded) и `extract_book(path, head, tail)`; `quality_score(fmt, ocr_used)`; встроенные метаданные: `_fb2_metadata`, `_epub_metadata` (`_epub_opf_path`). Функции форматов принимают `stats`.

## `llm.py`
Промпты и `CATEGORY_RULES` берутся из `config` (файлы `prompts/*.txt`).
- `_parse_categories` → `ALLOWED_CATEGORIES`; `DEFAULT_CATEGORY = "Требует внимания"`; `CATEGORY_RULES`; `FACTS_SCHEMA`, `CATEGORY_SCHEMA`.
- `_chat_raw` — единственный вызов Ollama (лестница параметров, `LLMUnavailableError`). `_chat_json` — опции + разбор JSON. `warm_up_model`.
- `extract_book_facts(text, filename)` → `{title, author, isbn, confidence, language_hint, series_hint}`.
- `decide_category(text, title, author, fb2_genres, subjects)` — точка входа классификации (`genres.fb2_decision` без LLM, иначе `classify_category` с `hints`), добавляет `source`.
- `classify_category(text, title, author)` → `{category, confidence, review_reason, evidence}`; факты включают `pub_year/publisher/edition`; низкая уверенность → `DEFAULT_CATEGORY`.
- `build_category_path(category, author, series)` — для `Художественные | Жанр` добавляет `author-slug | series-slug`.

## `normalization.py`
`normalize_spaces`, `is_unknown_label`, `to_ascii_slug` (кириллица → латиница), `canonical_author_name` (без отчеств/средних инициалов), `token_key` (без порядка слов), `normalize_for_match`, ISBN: `normalize_isbn`, `extract_isbn_candidates`, `extract_first_valid_isbn`. Имя файла: `parse_filename_hints` → `{title, author, series}`; `prefer_filename_title(title, author, filename)` → `(title, from_filename)`.

## `metadata_enricher.py`, `providers/`
`MetadataEnricher.enrich(facts)`: ISBN → `_lookup_isbn`, иначе `_lookup_title_author`; кэш `metadata_cache` (`_cache_get/_cache_set`); `_merge` — внешние данные заполняют только пустые title/author, ISBN/серию перекрывают. Провайдеры: `providers/openlibrary.py`, `providers/google_books.py`, база и скоринг — `providers/base.py` (`ProviderResult`, `score_candidate`, `best_candidate`).

## `config.py`
`_load_dotenv` (setdefault — реальные переменные окружения главнее), геттеры `_get_str/_int/_float/_bool`, константы настроек, `read_prompt`, `PROMPTS_DIR`; `CATEGORY_TREE`, `CATEGORY_RULES` и промпты читаются из `prompts/*.txt`; после T63 — ещё и `settings.override.env`.

## `preflight.py`
`run_preflight(extensions, input, output, dry_run)` → список проблем: `check_folders`, `check_tesseract`, `check_djvu_tools`, `check_ollama`.

## `genres.py`
Точка входа классификации по метаданным: `DECISIVE_FB2_GENRES`, `fb2_decision(genres)` → категория или `""`, `metadata_hints(fb2_genres, subjects)`. Не импортирует `llm`.
