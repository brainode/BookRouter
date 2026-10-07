# Codemap — BookRouter

Карта кода для навигации без чтения всего репозитория. Ищи функции по имени (`Grep "def name"`), номера строк здесь не приводятся — они устаревают.

Подкарты:
- [codemap/pipeline.md](codemap/pipeline.md) — конвейер сортировки: обнаружение → извлечение → факты → обогащение → классификация → копирование.
- [codemap/storage.md](codemap/storage.md) — SQLite (`db.py`), схема, миграции, файловые операции в библиотеке (`utils.py`, `library_ops.py`).
- [codemap/webui.md](codemap/webui.md) — веб-интерфейс (`webui/`), маршруты, шаблоны.
- [codemap/tests.md](codemap/tests.md) — какие тесты что покрывают и как писать новые.

Задачи: `TASKS.md` (порядок и статус) + `tasks/T*.md` (подробные спецификации). Оба локальные, в git не входят.

## Файлы верхнего уровня

| Файл | За что отвечает | Подкарта |
|---|---|---|
| `__main__.py` | CLI, оркестрация конвейера, запись результатов в БД | pipeline |
| `config.py` | загрузка `.env`, все настройки-константы, дерево категорий | pipeline |
| `sources.py` | обход входной папки и zip, `BookSource` | pipeline |
| `reader.py` | извлечение текста PDF/DjVu/EPUB/FB2, OCR | pipeline |
| `llm.py` | Ollama: факты, классификация, путь категории | pipeline |
| `normalization.py` | ISBN, slug, имена авторов, разбор имени файла | pipeline |
| `metadata_enricher.py`, `providers/` | OpenLibrary / Google Books + кэш | pipeline |
| `preflight.py` | проверка окружения перед прогоном | pipeline |
| `interrupt.py` | `stop_event`, `check_interrupted` (Ctrl+C) | pipeline |
| `logging_utils.py` | логгер `bookrouter`, `print` → лог | pipeline |
| `db.py` | `BookDB`: схема, миграции, запросы | storage |
| `utils.py` | пути Windows, хеши, копирование, `Errors` | storage |
| `review_books.py` | CLI очереди `needs_review` | storage |
| `export_results.py`, `truncate_db.py` | экспорт CSV, очистка БД | storage |
| `tests/` | pytest | tests |

Файлы, которые появятся по задачам (см. `TASKS.md`): `prompts/` (T40), `genres.py` (T44), `authors.py` (T47), `library_ops.py` + `library.py` (T48), `editions.py` (T50), `eval_quality.py` (T52), `webui/` + `webui.py` (T53+). Когда задача сделана, её модуль описывается в подкарте, а пометка «появится» снимается.

## Рецепты «что менять, если…»

| Изменение | Где |
|---|---|
| Новая колонка в `books` | `db.py`: `CREATE_TABLE_SQL`, `BOOK_COLUMN_MIGRATIONS`, оба SQL в `BookDB.upsert_book` + параметр; `__main__.py`: `_error_result`, словарь `result` в `process_file`, `_store_result` |
| Новый статус книги | `__main__.py`: `process_file`, `_attach_error_fallback` (копировать ли в `Errors`), счётчики в `main`; `db.py`: `get_all_origin_keys` (какие статусы считать готовыми), `_select_sources` |
| Новая настройка | `config.py` (константа с дефолтом) + `.env.example`; после T63 — список `MANAGED_KEYS` в `webui/settings_store.py` |
| Новая категория | после T40 — `prompts/category_tree.txt` и правило в `prompts/category_rules.txt`; до T40 — `config.CATEGORY_TREE`, `llm.CATEGORY_RULES` |
| Текст промпта | после T40 — `prompts/*.txt`; до — строки в `llm.extract_book_facts` / `llm.classify_category` |
| Новый формат книги | `sources.SUPPORTED_BOOK_EXTENSIONS`, `reader.detect_format`, `reader.extract_text_with_ends`, `preflight.run_preflight` |
| Новый провайдер метаданных | `providers/` (подкласс `MetadataProvider`), `providers/__init__.py`, `MetadataEnricher._build_providers` |
| Перенос/переименование файла в библиотеке | только через `library_ops.move_book` (T48), никогда `os.rename` напрямую |
| Новая страница веб-интерфейса | `webui/routes/<area>.py` + `webui/templates/<area>/*.html` + пункт в `webui/templates/base.html` (см. codemap/webui.md) |
