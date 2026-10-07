# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 The ScanBookShelf Authors
"""Сопоставление жанров FB2 категориям CATEGORY_TREE."""

FICTION_SF = "Художественные | Научная фантастика"
FICTION_FANTASY = "Художественные | Фэнтези"
FICTION_HORROR = "Художественные | Ужасы"
FICTION_DETECTIVE = "Художественные | Детектив"
FICTION_CHILDREN = "Художественные | Детская"
FICTION_CLASSIC = "Художественные | Классика"
FICTION_DRAMA = "Художественные | Драма"

# Конкретный тег однозначно задаёт категорию
DECISIVE_FB2_GENRES: dict[str, str] = {
    "sf_horror": FICTION_HORROR, "horror": FICTION_HORROR,
    "sf_fantasy": FICTION_FANTASY, "foreign_fantasy": FICTION_FANTASY, "sf_heroic": FICTION_FANTASY,
    "sf_epic": FICTION_FANTASY, "sf_fantasy_city": FICTION_FANTASY, "fairy_fantasy": FICTION_FANTASY,
    "sf_space": FICTION_SF, "sf_social": FICTION_SF, "sf_action": FICTION_SF, "sf_cyberpunk": FICTION_SF,
    "foreign_sf": FICTION_SF, "sf_postapocalyptic": FICTION_SF, "sf_history": FICTION_SF,
    "child_tale": FICTION_CHILDREN, "child_prose": FICTION_CHILDREN, "children": FICTION_CHILDREN,
    "child_verse": FICTION_CHILDREN, "child_adv": FICTION_CHILDREN, "child_sf": FICTION_CHILDREN,
    "child_det": FICTION_CHILDREN, "child_education": FICTION_CHILDREN,
    "detective": FICTION_DETECTIVE, "foreign_detective": FICTION_DETECTIVE, "det_classic": FICTION_DETECTIVE,
    "det_police": FICTION_DETECTIVE, "det_history": FICTION_DETECTIVE, "det_action": FICTION_DETECTIVE,
    "det_irony": FICTION_DETECTIVE, "det_espionage": FICTION_DETECTIVE, "thriller": FICTION_DETECTIVE,
    "prose_classic": FICTION_CLASSIC, "antique": FICTION_CLASSIC, "antique_european": FICTION_CLASSIC,
    "antique_russian": FICTION_CLASSIC,
    "dramaturgy": FICTION_DRAMA, "drama": FICTION_DRAMA,
    "nonf_biography": "Биографии и мемуары",
    "nonf_publicism": "Публицистика", "nonf_criticism": "Публицистика",
    "sci_history": "История", "sci_chem": "Наука | Химия", "sci_biology": "Наука | Биология и медицина",
    "sci_medicine": "Наука | Биология и медицина", "sci_phys": "Наука | Физика", "sci_cosmos": "Наука | Космос",
    "sci_psychology": "Психология",
}


def fb2_decision(genres: list[str]) -> str:
    """Категория по тегам FB2 или '' — тогда решает LLM. Детские теги главнее остальных."""
    mapped = {DECISIVE_FB2_GENRES[g] for g in genres if g in DECISIVE_FB2_GENRES}
    if FICTION_CHILDREN in mapped:
        return FICTION_CHILDREN
    return mapped.pop() if len(mapped) == 1 else ""


def metadata_hints(fb2_genres: list[str], subjects: list[str]) -> str:
    """Строка подсказок для промпта классификации; '' если подсказок нет."""
    lines = []
    if fb2_genres:
        lines.append("FB2 genre tags: " + ", ".join(fb2_genres))
    if subjects:
        lines.append("Publisher subjects: " + "; ".join(subjects[:10]))
    return "\n".join(lines)
