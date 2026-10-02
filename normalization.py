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


_PATRONYMIC_RE = re.compile(r"^[А-ЯЁ][а-яё]+(?:ович|евич|ич|овна|евна|ична|инична)$")
_MIDDLE_INITIAL_RE = re.compile(r"^[A-ZА-ЯЁ]\.?$")


def canonical_author_name(name: str) -> str:
    """«Иван Иванович Петров» и «Иван Петров», «John Q. Public» и «John Public» — один автор:
    для папок отбрасываем отчество и средние инициалы."""
    words = normalize_spaces(name).split()
    if len(words) < 3:
        return normalize_spaces(name)
    kept = [words[0]] + [
        word for word in words[1:-1] if not (_PATRONYMIC_RE.match(word) or _MIDDLE_INITIAL_RE.match(word))
    ] + [words[-1]]
    return " ".join(kept)


def token_key(value: str) -> str:
    """Ключ без учёта порядка слов: «Петров Иван» == «Иван Петров»."""
    return "-".join(sorted(to_ascii_slug(value, fallback="").split("-")))


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
    token = re.sub(r"\[[^\]]*\]", "", token)
    token = re.sub(r"\b(?:19|20)\d{2}\b", "", token)
    token = re.sub(r"[_]+", " ", token)
    token = normalize_spaces(token).strip("-— ")
    # Точку после инициала («Смит Дж.», «Сидоров А.С.») сохраняем, остальные хвостовые точки убираем
    if token.endswith(".") and not re.search(r"(?:^|[\s.])[A-ZА-ЯЁ][a-zа-яё]{0,2}\.$", token):
        token = token.rstrip(". ")
    return token


# Скобки с иллюстратором/переводчиком/редакцией, томом или жанровой пометкой — не серия
_NOT_SERIES_RE = re.compile(
    r"^\s*(?:худ|худож|ил|илл|рис|пер|ред|сост|авт|изд|пересказ|обраб|ill|illus|trans|transl|ed|edition|vol|volume|том|т|кн|книга|часть|ч)"
    r"(?:\b|\.)"
    r"|^\s*(?:сборник|рассказы|повесть|повести|роман|сказки|стихи|антология|fb2|pdf|djvu|epub|ocr|scan)\s*$",
    re.IGNORECASE,
)

_GENRE_WORDS = {
    "сказка", "сказки", "водевиль", "пьеса", "пьесы", "поэма", "поэмы", "стихи", "стихотворения", "басни",
    "повесть", "повести", "рассказ", "рассказы", "роман", "романы", "новеллы", "очерки", "сборник", "и",
    "fiction", "novel", "stories", "poems", "short",
}

# «Смит Дж.», «А. С. Сидоров», «Иван Петров», «John Doe»
_INITIAL_RE = re.compile(r"^[A-ZА-ЯЁ][a-zа-яё]{0,2}\.$")
_NAME_WORD_RE = re.compile(r"^[A-ZА-ЯЁ][a-zа-яё'’\-]+$")


def _looks_like_person(token: str) -> bool:
    words = normalize_spaces(re.sub(r"(?<=\.)(?=\S)", " ", token)).split()
    if not 1 <= len(words) <= 4 or any(ch.isdigit() for ch in token):
        return False
    has_initial = any(_INITIAL_RE.match(word) for word in words)
    names = [word for word in words if _NAME_WORD_RE.match(word)]
    if has_initial:
        return len(names) + sum(bool(_INITIAL_RE.match(w)) for w in words) == len(words)
    return len(words) >= 2 and len(names) == len(words)


def _has_initials(token: str) -> bool:
    return bool(re.search(r"(?:^|[\s.])[A-ZА-ЯЁ][a-zа-яё]{0,2}\.", token))


def _author_is_left(left: str, right: str) -> bool:
    """Обычный порядок в библиотеках — «Автор - Название», но встречается и «Название - Автор»."""
    if "," in left:
        return True
    left_person, right_person = _looks_like_person(left), _looks_like_person(right)
    if left_person != right_person:
        return left_person
    if left_person and right_person and _has_initials(left) != _has_initials(right):
        return _has_initials(left)
    return left_person


def _series_from_brackets(base: str) -> str:
    candidates = re.findall(r"\[([^\[\]]{2,80})\]", base) + re.findall(r"\(([^()]{2,80})\)", base)
    for raw in candidates:
        value = normalize_spaces(raw.replace("_", " "))
        if re.fullmatch(r"[\d\s.,\-–—]*(?:19|20)?\d*[\d\s.,\-–—]*", value):
            continue  # год, номер
        if _NOT_SERIES_RE.search(value):
            continue
        tokens = [token for token in re.split(r"[\s,\-–—/]+", value.lower()) if token]
        if tokens and all(token in _GENRE_WORDS for token in tokens):
            continue  # «[Пьеса-сказка]», «(повесть, рассказы)»
        return value.strip("-—. ")
    return ""


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

    if len(parts) >= 2:
        left = _cleanup_filename_token(parts[0])
        right = _cleanup_filename_token(parts[1])

        if _author_is_left(left, right):
            author = _normalize_author_token(left)
            title = right
        else:
            title = left
            author = _normalize_author_token(right)
    elif parts:
        # «Название [2021] Автор»
        match = re.match(r"^(.*?)\s*\[(?:19|20)\d{2}\]\s*(.+)$", parts[0])
        if match and _looks_like_person(_cleanup_filename_token(match.group(2))):
            title = _cleanup_filename_token(match.group(1))
            author = _normalize_author_token(match.group(2))
        else:
            title = _cleanup_filename_token(parts[0])

    series = _series_from_brackets(base)

    title = title or "Неизвестное название"
    author = author or "Неизвестный автор"
    return {"title": title, "author": author, "series": series}
