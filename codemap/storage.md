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

Ключевые колонки `books`: `title`, `author`, `series`, `isbn_norm`, `category` (полный путь через ` | `), `status`, `error_reason`, `new_path` (файл в библиотеке), `content_hash`, `preview_text`, `category_confidence`, `metadata_source`, `category_evidence`, `category_source`, `pub_year`, `publisher`, `edition`, `book_format`, `page_count`, `has_text_layer`, `quality`, `fb2_genres`, `subjects` (T43).

Новые таблицы и колонки по задачам: T42/T43 — колонки извлечения и фактов; T47 — `authors`, `author_aliases`, `books.author_id`; `actions_log` (id, created_at, action, summary, payload_json, undone_at) — готово (T47); `db.DONE_STATUSES` — статусы «обработано»; T50 — `work_key`, `same_as_id`, `work_reviews`; T52 — `golden`. Каждая задача указывает точный SQL.

## `utils.py`

| Символ | Что делает |
|---|---|
| `long_path(path)` | префикс `\\?\` для длинных путей Windows — **все** файловые операции в библиотеке через него |
| `sanitize_filename(name)` | запрещённые символы, зарезервированные имена, точки в конце, длина 120 |
| `file_fingerprint`, `full_file_hash`, `find_duplicate` | быстрый отпечаток + подтверждение SHA-256 |
| `source_signature` | `(size, mtime_ns)` источника |
| `atomic_copy(src, dst)` | копия через временный файл; одинаковый файл переиспользуется; конфликт имён → ` (2)` |
| `_resolve_existing_subdir_name` | нечёткое переиспользование существующей папки (только уровни автора/серии худлита) |
| `category_destination(file, title, author, category, output, fuzzy_from_level=2)` | путь файла и части категории без побочных эффектов |
| `copy_book_to_category(file, title, author, category, output)` | папки из категории + имя `Название - Автор.ext` |
| `copy_to_errors`, `remove_stale_error_copy` | `<OUTPUT>/Errors/{file,zip}/…` |
| `write_results_csv`, `append_csv`, `CSV_HEADER` | CSV с разделителем `|` |

## `review_books.py`
`resolve_book(db, id, category, output, title, author, series)` — переносит книгу из `Требует внимания` в выбранную категорию, ставит `status='ok'`, `metadata_source='manual'`. CLI: список очереди или `--resolve ID --category …`.

Колонки `books.work_key` (индекс), `books.same_as_id`; таблица `work_reviews(work_key PK, decision, decided_at)`.

Таблица `golden` (эталон: book_id PK, title, author, category-база, pub_year, edition, verified_at) — T52; наполняется `review_books.py --verify ID...` (`eval_quality.add_golden`). Модуль `eval_quality.py`: `category_base`, `fb2_agreement`, `consistency`, `run_golden`, `sample_for_review`; CLI `python eval_quality.py fb2|consistency|run|sample`.

## `library_ops.py`
`plan_book_step`, `apply_steps`, `record_action`, `run_action`, `undo_action`, `trash_book`, `prune_missing`, `LibraryOpError`, `TRASH_FOLDER`, `DUPLICATES_FOLDER`. Единственное место, где книги перемещаются после копирования; шаги `book`/`row` пишутся в `actions_log`.

## `library.py`
CLI: `actions [--limit]`, `undo ACTION_ID`, `prune-missing [--apply]` (функции `cmd_<name>(db, args)`). Следующие задачи добавляют команды.

## Появятся по задачам
- `editions.py` (T50) — `work_key`, `edition_relation`, `quality_key`, `edition_label`.

## `authors.py` (T48)
Таблицы `authors(id,name,slug,genre,created_at)`, `author_aliases(alias_key PK,author_id,initial_key,name)`, колонка `books.author_id`. Функции: `name_parts`, `alias_key`, `initial_key`, `phonetic_key`, `get_author`, `create_author`, `add_alias`, `find_author`, `resolve_author`, `suggest_merges`. Слияние — `library_ops.merge_authors(db, target, sources, output)`. CLI `library.py`: `backfill-authors [--apply]`, `suggest-authors [--limit]`, `merge-authors TARGET SOURCE… [--apply]`, `backfill-genres [--apply]`, `rebuild-fiction [--apply] [--author ID]`, `set-author-genre AUTHOR_ID GENRE [--apply]`, `backfill-quality [--apply] [--limit N]`, `backfill-works [--apply]`, `dedupe-editions [--apply]`, `reclassify (--category PATH | --status needs_review) [--limit N] [--apply]` (cmd_reclassify, действие `reclassify`). `authors.category_path_for_book(db, base, author, series, author_id, dry_run) -> (путь, book_genre)`. `library_ops.resolve_same_edition(db, keep_id, other_ids, output)` — худшие в `_Дубли`, одно действие журнала. Колонка `books.book_genre` (жанр самой книги; путь худлита использует `authors.genre`). `authors.fiction_genre`, `genre_for_new_book`, `majority_genre`; `library_ops.set_author_genre(db, author_id, genre, output)`.

## Папки в OUTPUT
`<категория>/…` — книги; `Errors/{file,zip}/…` — ошибки; `Требует внимания/` — очередь проверки; `_Дубли/` (T50) — худшие экземпляры того же издания; `_Корзина/` (T48) — «удалённые» вручную.
