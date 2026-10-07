# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 The ScanBookShelf Authors

import json
import re
import urllib.parse
import urllib.request

from normalization import normalize_isbn
from .base import MetadataProvider, ProviderResult, best_candidate


class GoogleBooksProvider(MetadataProvider):
    name = "googlebooks"

    def __init__(self, timeout_sec: int = 8):
        self.timeout_sec = timeout_sec

    def _get_json(self, url: str) -> dict:
        request = urllib.request.Request(url, headers={"User-Agent": "BookRouter/1.0"})
        with urllib.request.urlopen(request, timeout=self.timeout_sec) as response:
            payload = response.read().decode("utf-8")
            return json.loads(payload)

    def _result_from_item(self, item: dict, confidence: float) -> ProviderResult:
        info = item.get("volumeInfo") or {}
        title = str(info.get("title", "")).strip()
        authors = info.get("authors") or []
        return ProviderResult(
            provider=self.name, title=title, author=str(authors[0]).strip() if authors else "",
            isbn=self._extract_isbn13(info), series=self._extract_series_from_title(title),
            confidence=confidence if title else 0.0, raw=item,
        )

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

        wanted = normalize_isbn(isbn)
        if not wanted:
            return None
        for item in data.get("items") or []:
            if not isinstance(item, dict):
                continue
            identifiers = (item.get("volumeInfo") or {}).get("industryIdentifiers") or []
            if any(normalize_isbn(str(identifier.get("identifier", ""))) == wanted for identifier in identifiers if isinstance(identifier, dict)):
                result = self._result_from_item(item, 0.82)
                result.isbn = wanted
                return result
        return None

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

        results = [self._result_from_item(item, 0.66) for item in data.get("items") or [] if isinstance(item, dict)]
        # A title match identifies a work, not the original book's edition/ISBN.
        for result in results:
            result.isbn = ""
        return best_candidate(results, title, author)
