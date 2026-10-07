# Codemap: тесты

`python -m pytest` (из корня; `pytest.ini`: `pythonpath=.`, `testpaths=tests`). Одиночный тест: `python -m pytest tests/test_reader.py::test_name`.

| Файл | Что покрывает |
|---|---|
| `tests/test_reader.py` | `detect_format`, `_head_tail_ranges`, `prepare_book_path`, EPUB/FB2 через `extract_text_with_ends` |
| `tests/test_pipeline.py` | `__main__.process_file` с подменой LLM/обогащения, временные ошибки, книги без текста; загрузка `__main__` через `importlib` (фикстура `main`) |
| `tests/test_categories.py` | `llm.classify_category` (с подменой `_chat_json`), `build_category_path` |
| `tests/test_db_utils_sources.py` | `BookDB` (UPSERT, миграции), утилиты путей, `collect_book_sources` |
| `tests/test_duplicates.py` | побайтовые дубли |
| `tests/test_output.py` | `copy_book_to_category` |
| `tests/test_metadata_providers.py` | провайдеры и `MetadataEnricher` без сети |
| `tests/test_normalization.py` | ISBN, slug, `parse_filename_hints` |
| `tests/test_review.py` | `review_books.resolve_book` |
| `tests/test_truncate_db.py` | `truncate_db` |

## Как писать тесты
- Никакой сети и Ollama: подменять `llm._chat_json` или функции в модуле `__main__` через `monkeypatch.setattr(main, "extract_book_facts", …)`.
- БД — `BookDB(str(tmp_path / "t.db"))`; файлы — в `tmp_path`.
- Фейковые книги: `%PDF-1.4` для PDF; FB2/EPUB собираются строкой/`zipfile` прямо в тесте (см. `test_reader.py`).
- Веб: `TestClient(create_app(db_path=...))`, папка вывода — `tmp_path`.
- Имя теста описывает поведение: `test_same_edition_worse_copy_is_not_copied`.
