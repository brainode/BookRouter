# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 The ScanBookShelf Authors

"""Издания и произведения: ключ произведения, отношение изданий, выбор лучшего экземпляра."""

import re

from authors import alias_key
from normalization import normalize_for_match

_ORDINALS_RU = {"второе": "2", "третье": "3", "четвертое": "4", "четвёртое": "4", "пятое": "5",
                "шестое": "6", "седьмое": "7", "восьмое": "8", "девятое": "9", "десятое": "10"}
_ORDINALS_EN = {"second": "2", "third": "3", "fourth": "4", "fifth": "5", "sixth": "6",
                "seventh": "7", "eighth": "8", "ninth": "9", "tenth": "10"}
_RU_WORDS = "|".join(_ORDINALS_RU)
_EN_WORDS = "|".join(_ORDINALS_EN)

# (регулярка, функция номера из совпадения)
_EDITION_PATTERNS = [
    (re.compile(r"(\d{1,2})\s*-?\s*(?:е|ое|e)\s+изд", re.I), lambda m: m.group(1)),
    (re.compile(rf"издание\s+({_RU_WORDS})", re.I), lambda m: _ORDINALS_RU[m.group(1).lower()]),
    (re.compile(rf"({_RU_WORDS})\s+издание", re.I), lambda m: _ORDINALS_RU[m.group(1).lower()]),
    (re.compile(r"(\d{1,2})(?:st|nd|rd|th)\s+(?:ed\.|edition)", re.I), lambda m: m.group(1)),
    (re.compile(rf"({_EN_WORDS})\s+edition", re.I), lambda m: _ORDINALS_EN[m.group(1).lower()]),
]

_VOLUME_RE = re.compile(
    r"(?:\bтом|\bт\.|\bvol\.?|\bvolume|\bчасть|\bч\.|\bкнига|\bкн\.|\bpart|\bbook)\s*(\d{1,3}|[ivxlc]{1,6})\b",
    re.I,
)
_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
_COPYRIGHT_RE = re.compile(r"(?:©|\(c\)|copyright)[^\d\n]{0,40}((?:19|20)\d{2})", re.I)
_ROMAN = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100}


def parse_edition(text: str) -> str:
    best = None
    for pattern, number in _EDITION_PATTERNS:
        m = pattern.search(text or "")
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), number(m))
    if best is None or best[1] == "1":
        return ""
    return best[1]


def _roman_to_int(value: str) -> int:
    total = 0
    digits = [_ROMAN[c] for c in value.lower()]
    for i, d in enumerate(digits):
        total += -d if i + 1 < len(digits) and d < digits[i + 1] else d
    return total


def volume_marker(title: str) -> str:
    m = _VOLUME_RE.search(title or "")
    if not m:
        return ""
    raw = m.group(1)
    return str(int(raw)) if raw.isdigit() else str(_roman_to_int(raw))


def title_core(title: str) -> str:
    text = title or ""
    for pattern, _ in _EDITION_PATTERNS:
        text = pattern.sub(" ", text)
    text = _VOLUME_RE.sub(" ", text)
    text = re.sub(r"\([^)]*\)", " ", text)
    text = re.sub(r"\[[^\]]*\]", " ", text)
    text = _YEAR_RE.sub(" ", text)
    return normalize_for_match(text)


def year_from_text(text: str) -> str:
    m = _COPYRIGHT_RE.search(text or "")
    return m.group(1) if m else ""


def work_key(author_id: int | None, author: str, title: str) -> str:
    author_part = f"a{author_id}" if author_id else f"n{alias_key(author or '')}"
    return f"{author_part}|{volume_marker(title)}|{title_core(title)}"


def split_work_key(key: str) -> tuple[str, str, str]:
    parts = (key or "").split("|", 2)
    while len(parts) < 3:
        parts.append("")
    return parts[0], parts[1], parts[2]


def same_work(key_a: str, key_b: str) -> bool:
    author_a, vol_a, core_a = split_work_key(key_a)
    author_b, vol_b, core_b = split_work_key(key_b)
    if not core_a or not core_b or author_a != author_b or vol_a != vol_b:
        return False
    if core_a == core_b:
        return True
    short, long_ = sorted((core_a, core_b), key=len)
    return len(short) >= 10 and long_.startswith(short)


def _year(value) -> int | None:
    m = re.search(r"\d{4}", str(value or ""))
    return int(m.group()) if m else None


def edition_relation(a: dict, b: dict) -> str:
    isbn_a, isbn_b = a.get("isbn_norm") or "", b.get("isbn_norm") or ""
    if isbn_a and isbn_b and isbn_a == isbn_b:
        return "same"
    ed_a, ed_b = str(a.get("edition") or ""), str(b.get("edition") or "")
    if ed_a and ed_b and ed_a != ed_b:
        return "different"
    year_a, year_b = _year(a.get("pub_year")), _year(b.get("pub_year"))
    if year_a and year_b and abs(year_a - year_b) > 1:
        return "different"
    if ed_a and ed_b:
        return "same"
    if year_a and year_b:
        pub_a = normalize_for_match(a.get("publisher") or "")
        pub_b = normalize_for_match(b.get("publisher") or "")
        if not pub_a or not pub_b or pub_a == pub_b:
            return "same"
    return "unknown"


def quality_key(row: dict) -> tuple:
    return (row.get("quality") or 0, row.get("page_count") or 0, row.get("source_size") or 0)


def edition_label(edition: str, year: str) -> str:
    parts = []
    if edition:
        parts.append(f"{edition}-е изд.")
    if year:
        parts.append(str(year))
    return ", ".join(parts)


def find_work_matches(db, key: str, new: dict, exclude_id: int | None = None) -> tuple[list[dict], list[dict]]:
    author_part = split_work_key(key)[0]
    rows = db.conn.execute(
        "SELECT * FROM books WHERE status IN ('ok', 'needs_review') AND work_key LIKE ? ORDER BY id",
        (author_part + "|%",),
    ).fetchall()
    same: list[dict] = []
    others: list[dict] = []
    for row in rows:
        row = dict(row)
        if row["id"] == exclude_id or not same_work(key, row.get("work_key") or ""):
            continue
        (same if edition_relation(new, row) == "same" else others).append(row)
    return same, others
