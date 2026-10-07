---
name: webui-dev
description: Web developer guide for the BookRouter web UI (FastAPI + Jinja2 + Bootstrap 5 + htmx, package webui/). Load before creating or editing anything in webui/, webui.py or tests/test_webui_*.py.
---

# BookRouter web UI development

Stack is fixed: FastAPI, Jinja2 templates, Bootstrap 5.3 (+ Bootstrap Icons), htmx 2. No React/Vue, no npm, no CDN — vendored files in `webui/static/vendor/`. Server binds to `127.0.0.1` only. Language of the UI: Russian.

## Layout
See `codemap/webui.md`. One router module per area in `webui/routes/`, one template folder per area in `webui/templates/<area>/`. SQL lives in `webui/queries.py` (functions take `conn: sqlite3.Connection`, return `list[dict]` / `dict`). Routes never contain SQL strings longer than one line and never touch the filesystem directly.

## Route pattern
```python
# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 The ScanBookShelf Authors

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from db import BookDB
from webui import queries
from webui.deps import flash, get_db, render

router = APIRouter(prefix="/books", tags=["books"])


@router.get("")
def list_books(request: Request, q: str = "", page: int = 1, db: BookDB = Depends(get_db)):
    rows, total = queries.search_books(db.conn, q=q, page=page)
    template = "books/_rows.html" if request.headers.get("HX-Request") else "books/list.html"
    return render(request, template, rows=rows, total=total, q=q, page=page)


@router.post("/{book_id}/move")
def move(request: Request, book_id: int, category: str = Form(...), db: BookDB = Depends(get_db)):
    ...  # call library_ops, never os.* here
    flash(request, "Книга перенесена", "success", undo_action_id=action_id)
    return RedirectResponse(f"/books/{book_id}", status_code=303)
```
- Full page for normal requests; fragment (`_name.html`) when the `HX-Request` header is present.
- Mutations: POST + redirect 303 (PRG). Errors from `library_ops`/`ValueError` → `flash(..., "danger")` and redirect back; never a 500 for user mistakes.
- Destructive or file-moving actions: Bootstrap modal confirm, then POST. Every such action returns an `undo_action_id` and the flash shows an «Отменить» button posting to `/actions/{id}/undo`.

## Templates
- Every page `{% extends "base.html" %}` and fills `{% block title %}` and `{% block content %}`.
- Bootstrap components only: `navbar`, `table table-sm table-hover align-middle`, `card`, `badge`, `list-group`, `form-select`, `modal`, `pagination`, `alert`. No custom CSS frameworks; small additions go to `webui/static/app.css`.
- Status badges: use the macro `status_badge(status)` from `webui/templates/_macros.html` (ok → `text-bg-success`, needs_review → `text-bg-warning`, duplicate/same_edition → `text-bg-secondary`, error_* → `text-bg-danger`).
- htmx: live search `hx-get="/books" hx-trigger="input changed delay:300ms, search" hx-target="#rows" hx-push-url="true"`; lazy tree nodes `hx-get` + `hx-swap="outerHTML"`.
- Escape everything (Jinja autoescape is on; never `|safe` on DB data). Paths shown with `<code>`.

## DB and settings
- `get_db()` dependency opens `BookDB(app.state.db_path)` per request and closes it in `finally`. The pipeline may write at the same time; WAL makes reads safe. Keep transactions short.
- Settings (output folder, model, …) are read per request via `webui.settings_store.current_settings()` — not from `config` constants, which are frozen at import time.

## Tests
`tests/test_webui_<area>.py` with `fastapi.testclient.TestClient(create_app(db_path=str(tmp_path / "t.db"), output_folder=str(tmp_path / "out")))`. Seed rows with `BookDB.upsert_book`. Check status codes, key text in the HTML, and DB/file effects of POSTs. No network, no Ollama (monkeypatch `llm._chat_json` when a route triggers classification).

## Checklist before reporting done
- Page reachable from the navbar; works at 375 px width (Bootstrap grid, `table-responsive`).
- Empty states have a short Russian explanation, not an empty table.
- `python -m pytest` passes.
