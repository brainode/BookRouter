# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 The ScanBookShelf Authors

"""Авторы библиотеки: варианты имени, папка автора, подсказки для ручного слияния."""

import re
import time

from normalization import canonical_author_name, is_unknown_label, normalize_spaces, to_ascii_slug, token_key

_INITIAL_WORD_RE = re.compile(r"^[A-ZА-ЯЁ]\.?$")


def _words(name: str) -> list[str]:
    # «А.С.Пушкин» → «А. С. Пушкин». canonical_author_name здесь не годится: он выбрасывает средний инициал,
    # и у «Чуковский К. И.» остался бы инициал «И».
    return normalize_spaces(re.sub(r"(?<=\.)(?=\S)", " ", name)).split()


def name_parts(name: str) -> tuple[str, str, bool]:
    """(имя или инициал, фамилия, имя_задано_инициалом)."""
    words = _words(name)
    initials = [w for w in words if _INITIAL_WORD_RE.match(w)]
    others = [w for w in words if not _INITIAL_WORD_RE.match(w)]
    if not others:
        return "", "", bool(initials)
    if initials:
        return initials[0].rstrip("."), max(others, key=len), True
    if len(others) == 1:
        return "", others[0], False
    return others[0], others[-1], False


def alias_key(name: str) -> str:
    return token_key(canonical_author_name(name))


def initial_key(name: str) -> str:
    """«фамилия:первая буква имени» в латинице; '' если чего-то нет."""
    given, surname, _ = name_parts(name)
    given_slug = to_ascii_slug(given, fallback="")
    surname_slug = to_ascii_slug(surname, fallback="")
    if not given_slug or not surname_slug:
        return ""
    return f"{surname_slug}:{given_slug[0]}"


_PHONETIC_REPLACEMENTS = [("ph", "f"), ("sch", "sh"), ("tch", "ch"), ("kh", "h"), ("ck", "k"), ("x", "ks"),
                          ("q", "k"), ("w", "v"), ("f", "v"), ("j", "y"), ("y", "i")]


def _phonetic_token(token: str) -> str:
    for old, new in _PHONETIC_REPLACEMENTS:
        token = token.replace(old, new)
    # sh/ch/zh сохраняем, остальное h → g (Harry/Гарри, Herbert/Герберт)
    token = token.replace("sh", "1").replace("ch", "2").replace("zh", "3").replace("h", "g")
    token = token.replace("1", "sh").replace("2", "ch").replace("3", "zh")
    if len(token) > 2:
        token = token[0] + re.sub(r"[aeiou]", "", token[1:-1]) + token[-1]
    return re.sub(r"(.)\1+", r"\1", token)


def phonetic_key(name: str) -> str:
    """Грубый ключ для подсказок слияния: Stephen King == Стивен Кинг. Не для автоматического слияния."""
    tokens = [t for t in to_ascii_slug(canonical_author_name(name), fallback="").split("-") if t]
    return "-".join(sorted(_phonetic_token(t) for t in tokens))


def get_author(db, author_id: int) -> dict | None:
    row = db.conn.execute("SELECT * FROM authors WHERE id = ?", (author_id,)).fetchone()
    return dict(row) if row else None


def _unique_slug(db, slug: str) -> str:
    candidate, n = slug, 1
    while db.conn.execute("SELECT 1 FROM authors WHERE slug = ?", (candidate,)).fetchone():
        n += 1
        candidate = f"{slug}-{n}"
    return candidate


def add_alias(db, author_id: int, name: str, commit: bool = True) -> None:
    db.conn.execute(
        "INSERT OR IGNORE INTO author_aliases(alias_key, author_id, initial_key, name) VALUES (?,?,?,?)",
        (alias_key(name), author_id, initial_key(name), name),
    )
    if commit:
        db.conn.commit()


def create_author(db, name: str, slug: str | None = None, commit: bool = True) -> dict:
    slug = _unique_slug(db, slug or to_ascii_slug(canonical_author_name(name), fallback="unknown-author"))
    cur = db.conn.execute(
        "INSERT INTO authors(name, slug, created_at) VALUES (?,?,?)",
        (name, slug, time.strftime("%Y-%m-%d %H:%M:%S")),
    )
    add_alias(db, cur.lastrowid, name, commit=False)
    if commit:
        db.conn.commit()
    return get_author(db, cur.lastrowid)


def find_author(db, name: str) -> dict | None:
    row = db.conn.execute("SELECT author_id FROM author_aliases WHERE alias_key = ?", (alias_key(name),)).fetchone()
    if row:
        return get_author(db, row["author_id"])
    ikey = initial_key(name)
    if ikey:
        ids = db.conn.execute(
            "SELECT DISTINCT author_id FROM author_aliases WHERE initial_key = ?", (ikey,)
        ).fetchall()
        if len(ids) == 1:
            return get_author(db, ids[0]["author_id"])
    return None


def resolve_author(db, name: str, create: bool = True) -> dict | None:
    if not name or is_unknown_label(name):
        return None
    author = find_author(db, name)
    if author:
        if create:
            add_alias(db, author["id"], name)
        return author
    return create_author(db, name) if create else None


def suggest_merges(db, limit: int = 50) -> list[dict]:
    authors = [dict(r) for r in db.conn.execute("SELECT * FROM authors ORDER BY id").fetchall()]
    counts = {r[0]: r[1] for r in db.conn.execute(
        "SELECT author_id, COUNT(*) FROM books WHERE author_id IS NOT NULL GROUP BY author_id")}
    for a in authors:
        a["books"] = counts.get(a["id"], 0)
    pairs = []
    for i, a in enumerate(authors):
        for b in authors[i + 1:]:
            reason = None
            if phonetic_key(a["name"]) and phonetic_key(a["name"]) == phonetic_key(b["name"]):
                reason = "звучит одинаково"
            else:
                ga, sa, _ = name_parts(a["name"])
                gb, sb, _ = name_parts(b["name"])
                sa_slug, sb_slug = to_ascii_slug(sa, fallback=""), to_ascii_slug(sb, fallback="")
                if sa_slug and sa_slug == sb_slug:
                    fa, fb = to_ascii_slug(ga, fallback=""), to_ascii_slug(gb, fallback="")
                    if not fa or not fb or fa[0] == fb[0]:
                        reason = "та же фамилия"
            if reason:
                pairs.append({"a": a, "b": b, "reason": reason})
    pairs.sort(key=lambda p: (p["reason"] != "звучит одинаково", -(p["a"]["books"] + p["b"]["books"])))
    return pairs[:limit]


FICTION_PREFIX = "Художественные | "


def fiction_genre(category: str) -> str:
    """'Ужасы' для 'Художественные | Ужасы | …', иначе ''."""
    parts = [p.strip() for p in category.split("|")]
    return parts[1] if len(parts) >= 2 and parts[0] == "Художественные" else ""


def genre_for_new_book(db, author_row: dict | None, book_genre: str, dry_run: bool) -> str:
    """Жанр для пути новой книги. Первый жанр автора закрепляется за ним."""
    if not author_row or not book_genre:
        return book_genre
    if author_row.get("genre"):
        return author_row["genre"]
    if not dry_run:
        db.conn.execute("UPDATE authors SET genre = ? WHERE id = ?", (book_genre, author_row["id"]))
        db.conn.commit()
    return book_genre


def majority_genre(genres: list[str], order: list[str]) -> str:
    """Самый частый жанр; «Другое» не считается, если есть другие; ничья — кто раньше в order."""
    genres = [g for g in genres if g]
    if not genres:
        return ""
    real = [g for g in genres if g != "Другое"]
    pool = real or genres
    counts: dict[str, int] = {}
    for g in pool:
        counts[g] = counts.get(g, 0) + 1
    rank = {g: i for i, g in enumerate(order)}
    return min(counts, key=lambda g: (-counts[g], rank.get(g, len(rank)), g))
