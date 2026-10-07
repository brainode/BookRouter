# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 The ScanBookShelf Authors

"""Перенос уже разложенных книг: файл + строка БД + журнал actions_log с откатом."""

import json
import logging
import os
import shutil
import time

from utils import category_destination, long_path, make_unique_destination

logger = logging.getLogger("bookrouter")

TRASH_FOLDER = "_Корзина"
DUPLICATES_FOLDER = "_Дубли"
SPECIAL_FOLDERS = (TRASH_FOLDER, DUPLICATES_FOLDER, "Errors", "Требует внимания")


class LibraryOpError(RuntimeError):
    """Действие нельзя выполнить или отменить; сообщение — для пользователя, по-русски."""


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _bs(path: str) -> str:
    return str(path).replace("/", "\\")


def _move_file(src: str, dst: str) -> str:
    if src == dst:
        return dst
    dst = make_unique_destination(dst)
    os.makedirs(long_path(os.path.dirname(dst)), exist_ok=True)
    try:
        os.replace(long_path(src), long_path(dst))
    except OSError:
        shutil.move(long_path(src), long_path(dst))
    return dst


def _remove_empty_parents(path: str, output_folder: str) -> None:
    root = os.path.normcase(os.path.normpath(output_folder))
    special = {os.path.normcase(os.path.normpath(os.path.join(output_folder, f))) for f in SPECIAL_FOLDERS}
    current = os.path.dirname(os.path.normpath(path))
    while True:
        norm = os.path.normcase(current)
        if norm == root or norm in special or not norm.startswith(root):
            return
        try:
            os.rmdir(long_path(current))
        except OSError:
            return
        current = os.path.dirname(current)


def plan_book_step(db, book_id: int, output_folder: str, *, category=None, title=None, author=None,
                   special_root=None, updates=None, fuzzy_from_level: int = 2) -> dict:
    row = db.conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
    if row is None:
        raise LibraryOpError(f"Книга #{book_id} не найдена")
    old_path = row["new_path"] or ""
    new_category = category if category is not None else row["category"]
    new_title = title if title is not None else row["title"]
    new_author = author if author is not None else row["author"]
    if not old_path or not os.path.isfile(long_path(old_path)):
        raise LibraryOpError(f"Файл книги #{book_id} не найден: {old_path}")
    if special_root:
        new_path = os.path.join(output_folder, special_root, os.path.relpath(old_path, output_folder))
    else:
        new_path = category_destination(old_path, new_title or "", new_author or "", new_category or "",
                                        output_folder, fuzzy_from_level)[0]
    changes = {}
    if new_category != row["category"]:
        changes["category"] = new_category
    if new_title != row["title"]:
        changes["title"] = new_title
    if new_author != row["author"]:
        changes["author"] = new_author
    changes.update(updates or {})
    return {"kind": "book", "book_id": book_id, "from": _bs(old_path), "to": _bs(new_path), "updates": changes}


def _row_values(db, table: str, key: str, key_value):
    row = db.conn.execute(f"SELECT * FROM {table} WHERE {key} = ?", (key_value,)).fetchone()
    return dict(row) if row else None


def _put_row(db, table: str, key: str, key_value, values: dict | None) -> None:
    if values is None:
        db.conn.execute(f"DELETE FROM {table} WHERE {key} = ?", (key_value,))
        return
    cols = list(values)
    db.conn.execute(
        f"INSERT OR REPLACE INTO {table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
        [values[c] for c in cols],
    )


def _set_book(db, book_id: int, values: dict) -> None:
    if not values:
        return
    cols = list(values)
    db.conn.execute(f"UPDATE books SET {', '.join(c + ' = ?' for c in cols)} WHERE id = ?",
                    [values[c] for c in cols] + [book_id])


def apply_steps(db, steps: list[dict], output_folder: str) -> list[dict]:
    moved: list[tuple[str, str]] = []
    old_paths: list[str] = []
    try:
        for step in steps:
            if step["kind"] == "book":
                row = db.conn.execute("SELECT * FROM books WHERE id = ?", (step["book_id"],)).fetchone()
                if row is None:
                    raise LibraryOpError(f"Книга #{step['book_id']} не найдена")
                cols = list(step["updates"]) + ["new_path"]
                step["before"] = {c: row[c] for c in dict.fromkeys(cols)}
                dst = _move_file(step["from"], step["to"])
                if dst != step["from"]:
                    moved.append((step["from"], dst))
                    old_paths.append(step["from"])
                step["to"] = _bs(dst)
                _set_book(db, step["book_id"], {**step["updates"], "new_path": step["to"]})
            else:
                step["before"] = _row_values(db, step["table"], step["key"], step["key_value"])
                _put_row(db, step["table"], step["key"], step["key_value"], step["after"])
        db.conn.commit()
    except Exception as exc:
        db.conn.rollback()
        for src, dst in reversed(moved):
            try:
                _move_file(dst, src)
            except OSError:
                logger.warning("rollback_move_failed src=%s dst=%s", dst, src)
        if isinstance(exc, LibraryOpError):
            raise
        raise LibraryOpError(str(exc)) from exc
    for path in old_paths:
        _remove_empty_parents(path, output_folder)
    return steps


def record_action(db, action: str, summary: str, steps: list[dict]) -> int:
    payload = json.dumps(
        {"steps": steps, "book_ids": sorted({s["book_id"] for s in steps if s["kind"] == "book"})},
        ensure_ascii=False,
    )
    cur = db.conn.execute(
        "INSERT INTO actions_log (created_at, action, summary, payload_json) VALUES (?,?,?,?)",
        (_now(), action, summary, payload),
    )
    db.conn.commit()
    return cur.lastrowid


def run_action(db, action: str, summary: str, steps: list[dict], output_folder: str) -> int:
    if not steps:
        raise LibraryOpError("Нечего делать")
    apply_steps(db, steps, output_folder)
    return record_action(db, action, summary, steps)


def undo_action(db, action_id: int, output_folder: str) -> None:
    row = db.conn.execute("SELECT * FROM actions_log WHERE id = ?", (action_id,)).fetchone()
    if row is None:
        raise LibraryOpError(f"Действие #{action_id} не найдено")
    if row["undone_at"]:
        raise LibraryOpError("Действие уже отменено")
    payload = json.loads(row["payload_json"])
    ids = set(payload.get("book_ids", []))
    for later in db.conn.execute(
        "SELECT id, payload_json FROM actions_log WHERE id > ? AND undone_at IS NULL ORDER BY id", (action_id,)
    ).fetchall():
        if ids & set(json.loads(later["payload_json"]).get("book_ids", [])):
            raise LibraryOpError(f"Сначала отмени более позднее действие #{later['id']}")
    steps = payload["steps"]
    for step in steps:
        if step["kind"] == "book" and step["from"] != step["to"] and os.path.exists(long_path(step["from"])):
            raise LibraryOpError(f"Нельзя отменить: путь уже занят: {step['from']}")
    moved: list[tuple[str, str]] = []
    try:
        for step in reversed(steps):
            if step["kind"] == "book":
                if step["from"] != step["to"]:
                    _move_file(step["to"], step["from"])
                    moved.append((step["to"], step["from"]))
                _set_book(db, step["book_id"], step["before"])
            else:
                _put_row(db, step["table"], step["key"], step["key_value"], step["before"])
        db.conn.execute("UPDATE actions_log SET undone_at = ? WHERE id = ?", (_now(), action_id))
        db.conn.commit()
    except Exception as exc:
        db.conn.rollback()
        for src, dst in reversed(moved):
            try:
                _move_file(dst, src)
            except OSError:
                pass
        raise LibraryOpError(str(exc)) from exc
    for step in steps:
        if step["kind"] == "book" and step["from"] != step["to"]:
            _remove_empty_parents(step["to"], output_folder)


def trash_book(db, book_id: int, output_folder: str) -> int:
    step = plan_book_step(db, book_id, output_folder, special_root=TRASH_FOLDER, updates={"status": "trashed"})
    title = db.conn.execute("SELECT title FROM books WHERE id = ?", (book_id,)).fetchone()["title"]
    return run_action(db, "trash", f"В корзину: {title}", [step], output_folder)


def prune_missing(db, apply: bool) -> list[dict]:
    found = []
    for row in db.conn.execute("SELECT * FROM books WHERE status LIKE 'error_%'").fetchall():
        src = row["archive_path"] if row["origin_type"] == "zip" else row["origin_path"]
        if src and os.path.exists(long_path(src)):
            continue
        found.append({"id": row["id"], "original_path": row["original_path"], "status": row["status"]})
    if apply and found:
        db.conn.executemany("DELETE FROM books WHERE id = ?", [(f["id"],) for f in found])
        db.conn.commit()
    return found
