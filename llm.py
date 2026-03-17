# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 The ScanBookShelf Authors

import json
import re
import time
from typing import Any

from ollama import chat

from config import (
    CATEGORY_TREE,
    LLM_KEEP_ALIVE,
    LLM_NUM_CTX,
    LLM_NUM_PREDICT,
    LLM_REQUEST_DELAY_SEC,
    LLM_TEMPERATURE,
    MODEL_NAME,
)
from normalization import (
    extract_first_valid_isbn,
    is_unknown_label,
    normalize_spaces,
    parse_filename_hints,
    to_ascii_slug,
)

last_request_time = 0.0


def _parse_categories(raw_tree: str) -> list[str]:
    categories: list[str] = []
    for raw_line in raw_tree.splitlines():
        line = raw_line.strip()
        if not line or line.lower().startswith("уровень"):
            continue
        line = re.sub(r"\t+", "|", line)
        parts = [part.strip() for part in line.split("|") if part.strip()]
        if parts:
            categories.append(" | ".join(parts))
    return list(dict.fromkeys(categories))


ALLOWED_CATEGORIES = _parse_categories(CATEGORY_TREE)
DEFAULT_CATEGORY = (
    "Художественные | Другое"
    if "Художественные | Другое" in ALLOWED_CATEGORIES
    else (ALLOWED_CATEGORIES[0] if ALLOWED_CATEGORIES else "Другое")
)

FACTS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "author": {"type": "string"},
        "isbn": {"type": "string"},
        "confidence": {"type": "number"},
        "language_hint": {"type": "string"},
    },
    "required": ["title", "author", "isbn", "confidence", "language_hint"],
    "additionalProperties": False,
}

CATEGORY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "category": {"type": "string", "enum": ALLOWED_CATEGORIES or [DEFAULT_CATEGORY]},
        "confidence": {"type": "number"},
    },
    "required": ["category", "confidence"],
    "additionalProperties": False,
}


def _safe_json_loads(raw: str) -> dict[str, Any]:
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(0))
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass
    return {}


def _chat_json(system_prompt: str, user_prompt: str, schema: dict[str, Any] | None) -> dict[str, Any]:
    global last_request_time

    if LLM_REQUEST_DELAY_SEC > 0:
        elapsed = time.time() - last_request_time
        if elapsed < LLM_REQUEST_DELAY_SEC:
            time.sleep(LLM_REQUEST_DELAY_SEC - elapsed)

    options = {
        "temperature": LLM_TEMPERATURE,
        "num_ctx": LLM_NUM_CTX,
        "num_predict": LLM_NUM_PREDICT,
    }
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    raw = ""
    try:
        raw = chat(
            model=MODEL_NAME,
            messages=messages,
            options=options,
            format=schema,
            keep_alive=LLM_KEEP_ALIVE,
        )["message"]["content"].strip()
    except TypeError:
        try:
            raw = chat(
                model=MODEL_NAME,
                messages=messages,
                options=options,
                format=schema,
            )["message"]["content"].strip()
        except Exception:
            raw = chat(model=MODEL_NAME, messages=messages, options=options)["message"]["content"].strip()
    except Exception:
        raw = chat(model=MODEL_NAME, messages=messages, options=options)["message"]["content"].strip()

    last_request_time = time.time()
    return _safe_json_loads(raw)


def _normalize_confidence(value: Any, default: float = 0.5) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        result = default
    return max(0.0, min(1.0, result))


def _fallback_extract_from_text(text: str) -> tuple[str, str]:
    title = ""
    author = ""
    title_patterns = [
        r"(?im)^\s*title\s*[:\-]\s*(.+)$",
        r"(?im)^\s*название\s*[:\-]\s*(.+)$",
    ]
    author_patterns = [
        r"(?im)^\s*author\s*[:\-]\s*(.+)$",
        r"(?im)^\s*автор\s*[:\-]\s*(.+)$",
    ]

    for pattern in title_patterns:
        match = re.search(pattern, text)
        if match:
            title = normalize_spaces(match.group(1))
            break
    for pattern in author_patterns:
        match = re.search(pattern, text)
        if match:
            author = normalize_spaces(match.group(1))
            break
    return title, author


def extract_book_facts(text: str, filename: str = "", interrupted_flag: bool | None = None) -> dict[str, Any]:
    if interrupted_flag:
        return {
            "title": "Неизвестное название",
            "author": "Неизвестный автор",
            "isbn": "",
            "confidence": 0.0,
            "language_hint": "",
            "series_hint": "",
        }

    file_hints = parse_filename_hints(filename)
    excerpt = text[:12000]
    prompt = (
        "Extract bibliographic facts from the provided text.\n"
        "Return JSON only.\n"
        "If uncertain, keep the field empty.\n\n"
        f"Filename hint:\n{filename}\n\n"
        f"Text:\n{excerpt}"
    )
    system_prompt = (
        "You are a metadata extractor. Focus on title, author, and ISBN.\n"
        "Do not classify category.\n"
        "Do not invent values."
    )
    model_data = _chat_json(system_prompt, prompt, FACTS_SCHEMA)

    fallback_title, fallback_author = _fallback_extract_from_text(excerpt)

    title = normalize_spaces(str(model_data.get("title", ""))) or fallback_title
    author = normalize_spaces(str(model_data.get("author", ""))) or fallback_author
    isbn = normalize_spaces(str(model_data.get("isbn", "")))
    confidence = _normalize_confidence(model_data.get("confidence"), default=0.45)
    language_hint = normalize_spaces(str(model_data.get("language_hint", "")))

    if not title or is_unknown_label(title):
        title = file_hints["title"]
        confidence = min(confidence, 0.55)
    if not author or is_unknown_label(author):
        author = file_hints["author"]
        confidence = min(confidence, 0.55)

    isbn_from_text = extract_first_valid_isbn(isbn or excerpt)
    if isbn_from_text:
        isbn = isbn_from_text

    return {
        "title": title or "Неизвестное название",
        "author": author or "Неизвестный автор",
        "isbn": isbn or "",
        "confidence": confidence,
        "language_hint": language_hint,
        "series_hint": file_hints.get("series", ""),
    }


def classify_category(text: str, title: str, author: str, interrupted_flag: bool | None = None) -> dict[str, Any]:
    if interrupted_flag:
        return {"category": DEFAULT_CATEGORY, "confidence": 0.0}

    categories_text = "\n".join(f"- {category}" for category in ALLOWED_CATEGORIES)
    prompt = (
        "Classify this book into exactly one category from the list.\n"
        "Return JSON only.\n\n"
        f"Title: {title}\n"
        f"Author: {author}\n"
        f"Text excerpt:\n{text[:9000]}"
    )
    system_prompt = (
        "You are a strict book classifier.\n"
        "Choose one category from the provided list.\n"
        "No extra explanations.\n"
        f"Allowed categories:\n{categories_text}"
    )
    model_data = _chat_json(system_prompt, prompt, CATEGORY_SCHEMA)
    category = normalize_spaces(str(model_data.get("category", "")))
    confidence = _normalize_confidence(model_data.get("confidence"), default=0.5)

    if category not in ALLOWED_CATEGORIES:
        # Soft normalization by case-insensitive comparison.
        category_lc = category.lower()
        for item in ALLOWED_CATEGORIES:
            if item.lower() == category_lc:
                category = item
                break
        else:
            category = DEFAULT_CATEGORY

    return {"category": category, "confidence": confidence}


def build_category_path(category: str, author: str, series: str) -> str:
    normalized_category = normalize_spaces(str(category)).replace(">", "|")
    parts = [normalize_spaces(part) for part in normalized_category.split("|") if normalize_spaces(part)]
    if not parts:
        return DEFAULT_CATEGORY

    if len(parts) >= 2 and parts[0] == "Художественные":
        author_key = to_ascii_slug(author, fallback="unknown-author")
        series_key = to_ascii_slug(series or "Без серии", fallback="bez-serii")
        return " | ".join([parts[0], parts[1], author_key, series_key])

    return " | ".join(parts)


def get_metadata(text: str, interrupted_flag: bool | None = None, filename: str = "") -> dict[str, Any]:
    # Backward-compatible wrapper used by old flow.
    facts = extract_book_facts(text, filename, interrupted_flag)
    category = classify_category(text, facts["title"], facts["author"], interrupted_flag)
    category_path = build_category_path(category["category"], facts["author"], facts.get("series_hint", "Без серии"))

    return {
        "title": facts["title"],
        "author": facts["author"],
        "isbn": facts["isbn"],
        "series": facts.get("series_hint", ""),
        "category": category["category"],
        "category_path": category_path,
        "confidence": min(1.0, max(facts["confidence"], category["confidence"])),
    }
