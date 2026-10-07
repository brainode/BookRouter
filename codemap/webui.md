# Codemap: веб-интерфейс (`webui/`)

> Статус: **запланирован** (T53–T63). Структура ниже — обязательная; задачи создают файлы именно так. Когда файл создан, сними пометку «план» в его строке.

Стек: FastAPI + Jinja2 + Bootstrap 5.3 + Bootstrap Icons + htmx 2 (все статические файлы лежат локально в `webui/static/vendor/`, без CDN). Сервер: `python webui.py` → uvicorn на `127.0.0.1:8765`. Правила разработки — скилл `.claude/skills/webui-dev`.

## Структура

| Путь | Назначение |
|---|---|
| `webui.py` | точка входа: аргументы `--host --port --db`, запуск uvicorn (план) |
| `webui/__init__.py` | пустой (план) |
| `webui/app.py` | `create_app(db_path) -> FastAPI`: шаблоны, статика, подключение роутеров (план) |
| `webui/deps.py` | `get_db()` — `BookDB` на запрос; `templates`; `render(request, name, **ctx)` (план) |
| `webui/settings_store.py` | чтение/запись `settings.override.env`, `MANAGED_KEYS`, `current_settings()` (план, T63) |
| `webui/queries.py` | все SELECT для страниц (чистые функции от `conn`) (план) |
| `webui/jobs.py` | `JobManager` — фоновые процессы сортировки и прогона эталона: старт/стоп/прогресс (план, T61) |
| `webui/routes/dashboard.py` | `/` — сводка (план, T53) |
| `webui/routes/books.py` | `/books`, `/books/{id}`, правка, открыть файл (план, T54, T58) |
| `webui/routes/categories.py` | `/categories` (план, T55) |
| `webui/routes/editions.py` | `/editions` — группы произведений и дубли (план, T56, T60) |
| `webui/routes/review.py` | `/review`, `/errors` (план, T57) |
| `webui/routes/authors.py` | `/authors` — слияние, жанр (план, T59) |
| `webui/routes/actions.py` | `/actions` — журнал и откат (план, T58) |
| `webui/routes/scan.py` | `/scan` (план, T61) |
| `webui/routes/upload.py` | `/upload` (план, T62) |
| `webui/routes/settings.py` | `/settings` (план, T63) |
| `webui/templates/base.html` | layout, navbar, контейнер flash-сообщений (план) |
| `webui/templates/<area>/*.html` | страницы; `_*.html` — htmx-фрагменты (план) |
| `webui/static/app.css`, `app.js` | свои стили/скрипты (план) |
| `tests/test_webui_*.py` | тесты через `fastapi.testclient.TestClient` (план) |

## Правила
- Чтение — через `webui/queries.py`; изменение файлов/книг — только через `library_ops` (T48) и `review_books.resolve_book`.
- Любое действие, меняющее файлы, — POST-форма с подтверждением, запись в `actions_log`, flash-сообщение с ссылкой «Отменить».
