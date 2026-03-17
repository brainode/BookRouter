# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 The ScanBookShelf Authors

import json
import re
import urllib.parse
import urllib.request

from .base import MetadataProvider, ProviderResult


class GoogleBooksProvider(MetadataProvider):
    name = "googlebooks"

    def __init__(self, timeout_sec: int = 8):
        self.timeout_sec = timeout_sec

    def _get_json(self, url: str) -> dict:
        request = urllib.request.Request(url, headers={"User-Agent": "ScanBookShelf/1.0"})
        with urllib.request.urlopen(request, timeout=self.timeout_sec) as response:
            payload = response.read().decode("utf-8")
            return json.loads(payload)

    def _pick_first_item(self, data: dict) -> dict:
        items = data.get("items") or []
        return items[0] if items else {}

    def _extract_isbn13(self, volume_info: dict) -> str:
        identifiers = volume_info.get("industryIdentifiers") or []
        for item in identifiers:
            if not isinstance(item, dict):
                continue
            if str(item.get("type", "")).upper() == "ISBN_13":
                return str(item.get("identifier", "")).strip()
        for item in identifiers:
            if not isinstance(item, dict):
                continue
            candidate = str(item.get("identifier", "")).strip()
            if re.fullmatch(r"\d{13}", candidate):
                return candidate
        return ""

    def _extract_series_from_title(self, title: str) -> str:
        text = str(title or "").strip()
        if "#" in text:
            chunks = [part.strip() for part in text.split("#", 1)]
            if len(chunks) == 2 and len(chunks[0]) > 2:
                return chunks[0]
        return ""

    def lookup_by_isbn(self, isbn: str) -> ProviderResult | None:
        if not isbn:
            return None
        query = urllib.parse.quote(f"isbn:{isbn}")
        url = f"https://www.googleapis.com/books/v1/volumes?q={query}&maxResults=5"

        try:
            data = self._get_json(url)
        except Exception:
            return None

        item = self._pick_first_item(data)
        if not item:
            return None
        info = item.get("volumeInfo") or {}

        title = str(info.get("title", "")).strip()
        authors = info.get("authors") or []
        author = str(authors[0]).strip() if authors else ""
        isbn_value = self._extract_isbn13(info) or isbn
        series = self._extract_series_from_title(title)

        result = ProviderResult(
            provider=self.name,
            title=title,
            author=author,
            isbn=isbn_value,
            series=series,
            confidence=0.82 if title else 0.0,
            raw=item,
        )
        return result if not result.is_empty else None

    def lookup_by_title_author(self, title: str, author: str) -> ProviderResult | None:
        if not title:
            return None
        query_parts = [f"intitle:{title}"]
        if author:
            query_parts.append(f"inauthor:{author}")
        query = urllib.parse.quote(" ".join(query_parts))
        url = f"https://www.googleapis.com/books/v1/volumes?q={query}&maxResults=5"

        try:
            data = self._get_json(url)
        except Exception:
            return None

        item = self._pick_first_item(data)
        if not item:
            return None
        info = item.get("volumeInfo") or {}

        title_value = str(info.get("title", "")).strip()
        authors = info.get("authors") or []
        author_value = str(authors[0]).strip() if authors else ""
        isbn_value = self._extract_isbn13(info)
        series = self._extract_series_from_title(title_value)

        result = ProviderResult(
            provider=self.name,
            title=title_value,
            author=author_value,
            isbn=isbn_value,
            series=series,
            confidence=0.66 if title_value else 0.0,
            raw=item,
        )
        return result if not result.is_empty else None
