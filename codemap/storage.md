# Codemap: хранение и файлы библиотеки

## `db.py` — `BookDB`

БД `books.db` в текущей папке (`DB_FILE`). Соединение с `row_factory=sqlite3.Row`, WAL.

| Символ | Что делает |
|---|---|
| `CREATE_TABLE_SQL` | таблица `books` (одна строка = один источник, ключ — `origin_type`+`origin_path`) |
| `CREATE_FTS_SQL`, `CREATE_TRIGGERS_SQL` | FTS5 `books_fts(title, author, preview_text)` + триггеры синхронизации |
| `CREATE_METADATA_CACHE_SQL` | кэш провайдеров |
| `CREATE_INDEX_SQL` | индексы |
| `BOOK_COLUMN_MIGRATIONS` | колонки, добавляемые `ALTER TABLE` при старте (`_ensure_book_columns`) |
| `upsert_book(**fields)` | UPDATE по origin, иначе INSERT. **Два SQL** — при новой колонке править оба |
| `search_books(query)` | FTS-поиск |
| `get_all_origin_keys`, `get_review_origin_keys`, `get_source_states` | для `_select_sources` |
| `get_ok_hashes` | отпечаток → пути (поиск побайтовых дублей) |
| `find_book_by_origin` | `{id, status, new_path}` |
| `export_results(path)` | `results.csv` из БД |
| `get_cached_metadata`, `upsert_cached_metadata` | кэш провайдеров |

Ключевые колонки `books`: `title`, `author`, `series`, `isbn_norm`, `category` (полный путь через ` | `), `status`, `error_reason`, `new_path` (файл в библиотеке), `content_hash`, `preview_text`, `category_confidence`, `metadata_source`.

Новые таблицы и колонки по задачам: T42/T43 — колонки извлечения и фактов; T47 — `authors`, `author_aliases`, `books.author_id`; T48 — `actions_log`; T50 — `work_key`, `same_as_id`, `work_reviews`; T52 — `golden`. Каждая задача указывает точный SQL.

## `utils.py`

| Символ | Что делает |
|---|---|
| `long_path(path)` | префикс `\\?\` для длинных путей Windows — **все** файловые операции в библиотеке через него |
| `sanitize_filename(name)` | запрещённые символы, зарезервированные имена, точки в конце, длина 120 |
| `file_fingerprint`, `full_file_hash`, `find_duplicate` | быстрый отпечаток + подтверждение SHA-256 |
| `source_signature` | `(size, mtime_ns)` источника |
| `atomic_copy(src, dst)` | копия через временный файл; одинаковый файл переиспользуется; конфликт имён → ` (2)` |
| `_resolve_existing_subdir_name` | нечёткое переиспользование существующей папки (только уровни автора/серии худлита) |
| `copy_book_to_category(file, title, author, category, output)` | папки из категории + имя `Название - Автор.ext` |
| `copy_to_errors`, `remove_stale_error_copy` | `<OUTPUT>/Errors/{file,zip}/…` |
| `write_results_csv`, `append_csv`, `CSV_HEADER` | CSV с разделителем `|` |

## `review_books.py`
`resolve_book(db, id, category, output, title, author, series)` — переносит книгу из `Требует внимания` в выбранную категорию, ставит `status='ok'`, `metadata_source='manual'`. CLI: список очереди или `--resolve ID --category …`.

## Появятся по задачам
- `authors.py` (T47) — `resolve_author`, ключи имён, фонетические подсказки.
- `library_ops.py` (T48) — `move_book`, `log_action`, `undo_action`, `trash_book`; единственное место, где книги перемещаются после копирования.
- `library.py` (T48+) — CLI обслуживания: `merge-authors`, `suggest-authors`, `set-author-genre`, `rebuild-fiction`, `backfill-*`, `dedupe-editions`, `reclassify`, `prune-missing`, `undo`.
- `editions.py` (T50) — `work_key`, `edition_relation`, `quality_key`, `edition_label`.

## Папки в OUTPUT
`<категория>/…` — книги; `Errors/{file,zip}/…` — ошибки; `Требует внимания/` — очередь проверки; `_Дубли/` (T50) — худшие экземпляры того же издания; `_Корзина/` (T48) — «удалённые» вручную.
