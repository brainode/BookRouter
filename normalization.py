# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 The ScanBookShelf Authors

import re
import unicodedata

_CYRILLIC_TO_LATIN = {
    "а": "a",
    "б": "b",
    "в": "v",
    "г": "g",
    "д": "d",
    "е": "e",
    "ё": "yo",
    "ж": "zh",
    "з": "z",
    "и": "i",
    "й": "y",
    "к": "k",
    "л": "l",
    "м": "m",
    "н": "n",
    "о": "o",
    "п": "p",
    "р": "r",
    "с": "s",
    "т": "t",
    "у": "u",
    "ф": "f",
    "х": "h",
    "ц": "ts",
    "ч": "ch",
    "ш": "sh",
    "щ": "sch",
    "ъ": "",
    "ы": "y",
    "ь": "",
    "э": "e",
    "ю": "yu",
    "я": "ya",
}

UNKNOWN_VALUES = {
    "",
    "unknown",
    "unknown title",
    "unknown author",
    "неизвестно",
    "неизвестный",
    "неизвестное название",
    "неизвестный автор",
    "n/a",
    "none",
}


def normalize_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def is_unknown_label(value: str) -> bool:
    normalized = normalize_spaces(value).lower()
    return normalized in UNKNOWN_VALUES


def to_ascii_slug(value: str, fallback: str = "unknown") -> str:
    value = normalize_spaces(value)
    if not value:
        return fallback

    mapped = "".join(_CYRILLIC_TO_LATIN.get(ch, ch) for ch in value.lower())
    mapped = unicodedata.normalize("NFKD", mapped).encode("ascii", "ignore").decode("ascii")
    mapped = re.sub(r"[^a-z0-9]+", "-", mapped).strip("-")
    return mapped or fallback


def normalize_for_match(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", to_ascii_slug(value, fallback=""))


def _validate_isbn13(isbn13: str) -> bool:
    if len(isbn13) != 13 or not isbn13.isdigit():
        return False
    checksum = 0
    for idx, ch in enumerate(isbn13[:12]):
        n = int(ch)
        checksum += n if idx % 2 == 0 else n * 3
    check_digit = (10 - (checksum % 10)) % 10
    return check_digit == int(isbn13[-1])


def _validate_isbn10(isbn10: str) -> bool:
    if len(isbn10) != 10:
        return False
    if not re.match(r"^\d{9}[\dX]$", isbn10):
        return False
    checksum = 0
    for idx, ch in enumerate(isbn10):
        value = 10 if ch == "X" else int(ch)
        checksum += value * (10 - idx)
    return checksum % 11 == 0


def _isbn10_to_isbn13(isbn10: str) -> str:
    core = "978" + isbn10[:9]
    checksum = 0
    for idx, ch in enumerate(core):
        n = int(ch)
        checksum += n if idx % 2 == 0 else n * 3
    check_digit = (10 - (checksum % 10)) % 10
    return f"{core}{check_digit}"


def normalize_isbn(raw_isbn: str | None) -> str | None:
    if not raw_isbn:
        return None
    cleaned = re.sub(r"[^0-9Xx]", "", str(raw_isbn)).upper()
    if len(cleaned) == 13 and _validate_isbn13(cleaned):
        return cleaned
    if len(cleaned) == 10 and _validate_isbn10(cleaned):
        return _isbn10_to_isbn13(cleaned)
    return None


def extract_isbn_candidates(text: str) -> list[str]:
    if not text:
        return []
    pattern = r"\b(?:97[89][-\s]?)?\d[\dXx\-\s]{8,20}\d\b"
    candidates: list[str] = []
    for match in re.finditer(pattern, text):
        raw = match.group(0)
        normalized = normalize_isbn(raw)
        if normalized and normalized not in candidates:
            candidates.append(normalized)
    return candidates


def extract_first_valid_isbn(text: str) -> str | None:
    candidates = extract_isbn_candidates(text)
    return candidates[0] if candidates else None


def _cleanup_filename_token(token: str) -> str:
    token = normalize_spaces(token)
    token = re.sub(r"\([^)]*\)", "", token)
    token = re.sub(r"\b(?:19|20)\d{2}\b", "", token)
    token = re.sub(r"[_]+", " ", token)
    token = normalize_spaces(token)
    return token.strip("- ")


def _normalize_author_token(author: str) -> str:
    author = _cleanup_filename_token(author)
    if "," in author:
        parts = [normalize_spaces(p) for p in author.split(",") if normalize_spaces(p)]
        if len(parts) >= 2:
            return normalize_spaces(" ".join(parts[1:] + [parts[0]]))
    return author


def parse_filename_hints(filename: str) -> dict[str, str]:
    base = filename.rsplit(".", 1)[0]
    base = re.sub(r"^\s*\d+\s*", "", base)
    base = base.replace("—", "-")
    parts = [normalize_spaces(part) for part in re.split(r"\s+-\s+", base) if normalize_spaces(part)]

    title = ""
    author = ""
    series = ""

    if len(parts) >= 2:
        left = _cleanup_filename_token(parts[0])
        right = _cleanup_filename_token(parts[1])

        if "," in left:
            author = _normalize_author_token(left)
            title = right
        else:
            title = left
            author = _normalize_author_token(right)
    elif parts:
        title = _cleanup_filename_token(parts[0])

    series_match = re.search(r"\(([^()]{2,80})\)", base)
    if series_match:
        series = _cleanup_filename_token(series_match.group(1))

    title = title or "Неизвестное название"
    author = author or "Неизвестный автор"
    return {"title": title, "author": author, "series": series}
