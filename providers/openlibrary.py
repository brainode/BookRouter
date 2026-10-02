# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 The ScanBookShelf Authors

import json
import urllib.parse
import urllib.request

from .base import MetadataProvider, ProviderResult


class OpenLibraryProvider(MetadataProvider):
    name = "openlibrary"

    def __init__(self, timeout_sec: int = 8):
        self.timeout_sec = timeout_sec

    def _get_json(self, url: str) -> dict:
        request = urllib.request.Request(url, headers={"User-Agent": "BookRouter/1.0"})
        with urllib.request.urlopen(request, timeout=self.timeout_sec) as response:
            payload = response.read().decode("utf-8")
            return json.loads(payload)

    def _fetch_author_name(self, author_key: str) -> str:
        try:
            data = self._get_json(f"https://openlibrary.org{author_key}.json")
            return str(data.get("name", "")).strip()
        except Exception:
            return ""

    def _fetch_work(self, work_key: str) -> dict:
        try:
            return self._get_json(f"https://openlibrary.org{work_key}.json")
        except Exception:
            return {}

    def lookup_by_isbn(self, isbn: str) -> ProviderResult | None:
        if not isbn:
            return None
        try:
            book = self._get_json(f"https://openlibrary.org/isbn/{urllib.parse.quote(isbn)}.json")
        except Exception:
            return None

        title = str(book.get("title", "")).strip()
        author = ""
        authors = book.get("authors") or []
        if authors and isinstance(authors[0], dict):
            key = str(authors[0].get("key", ""))
            if key:
                author = self._fetch_author_name(key)

        series = ""
        series_index = ""
        works = book.get("works") or []
        if works and isinstance(works[0], dict):
            work_key = str(works[0].get("key", "")).strip()
            if work_key:
                work = self._fetch_work(work_key)
                series_field = work.get("series")
                if isinstance(series_field, list) and series_field:
                    series = str(series_field[0]).strip()
                elif isinstance(series_field, str):
                    series = series_field.strip()
                position = work.get("series_position")
                if isinstance(position, (str, int, float)):
                    series_index = str(position).strip()

        result = ProviderResult(
            provider=self.name,
            title=title,
            author=author,
            isbn=isbn,
            series=series,
            series_index=series_index,
            confidence=0.93 if title else 0.75,
            raw=book,
        )
        return result if not result.is_empty else None

    def lookup_by_title_author(self, title: str, author: str) -> ProviderResult | None:
        if not title:
            return None
        params = {
            "title": title,
            "limit": 5,
        }
        if author:
            params["author"] = author
        query = urllib.parse.urlencode(params)
        url = f"https://openlibrary.org/search.json?{query}"

        try:
            data = self._get_json(url)
        except Exception:
            return None

        docs = data.get("docs") or []
        if not docs:
            return None
        doc = docs[0]

        title_value = str(doc.get("title", "")).strip()
        author_names = doc.get("author_name") or []
        author_value = str(author_names[0]).strip() if author_names else ""
        isbns = doc.get("isbn") or []
        isbn_value = str(isbns[0]).strip() if isbns else ""
        series_list = doc.get("series") or []
        series_value = str(series_list[0]).strip() if series_list else ""

        result = ProviderResult(
            provider=self.name,
            title=title_value,
            author=author_value,
            isbn=isbn_value,
            series=series_value,
            confidence=0.74 if title_value else 0.0,
            raw=doc,
        )
        return result if not result.is_empty else None
