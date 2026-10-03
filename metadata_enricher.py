# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 The ScanBookShelf Authors

import json
import time
from typing import Any

from config import (
    ENRICH_CACHE_TTL_DAYS,
    ENRICH_ENABLED,
    ENRICH_MIN_MATCH_SCORE,
    ENRICH_PROVIDERS,
    ENRICH_RETRIES,
    ENRICH_HTTP_TIMEOUT,
)
from normalization import (
    is_unknown_label,
    normalize_isbn,
    normalize_spaces,
)
from providers import GoogleBooksProvider, OpenLibraryProvider, ProviderResult
from providers.base import score_candidate


class MetadataEnricher:
    def __init__(self, db=None):
        self.db = db
        self.enabled = ENRICH_ENABLED
        self.retry_count = max(1, int(ENRICH_RETRIES))
        self.ttl_days = max(1, int(ENRICH_CACHE_TTL_DAYS))
        self.min_match_score = float(ENRICH_MIN_MATCH_SCORE)
        self.providers = self._build_providers()

    def _build_providers(self):
        raw = str(ENRICH_PROVIDERS or "")
        requested = [item.strip().lower() for item in raw.split(",") if item.strip()]
        if not requested:
            requested = ["openlibrary", "googlebooks"]

        providers = []
        for name in requested:
            if name == "openlibrary":
                providers.append(OpenLibraryProvider(timeout_sec=ENRICH_HTTP_TIMEOUT))
            elif name == "googlebooks":
                providers.append(GoogleBooksProvider(timeout_sec=ENRICH_HTTP_TIMEOUT))
        return providers

    def _cache_get(self, query_key: str) -> ProviderResult | None:
        if not self.db or not hasattr(self.db, "get_cached_metadata"):
            return None
        row = self.db.get_cached_metadata(query_key)
        if not row:
            return None
        try:
            payload = json.loads(row["payload_json"])
            return ProviderResult(
                provider=row["provider"],
                title=str(payload.get("title", "")),
                author=str(payload.get("author", "")),
                isbn=str(payload.get("isbn", "")),
                series=str(payload.get("series", "")),
                series_index=str(payload.get("series_index", "")),
                confidence=float(payload.get("confidence", 0.0)),
                match_score=float(payload.get("match_score", 0.0)),
                raw=payload.get("raw", {}) if isinstance(payload.get("raw"), dict) else {},
            )
        except Exception:
            return None

    def _cache_set(self, query_key: str, result: ProviderResult):
        if not self.db or not hasattr(self.db, "upsert_cached_metadata"):
            return
        payload = {
            "title": result.title,
            "author": result.author,
            "isbn": result.isbn,
            "series": result.series,
            "series_index": result.series_index,
            "confidence": result.confidence,
            "match_score": result.match_score,
            "raw": result.raw,
        }
        self.db.upsert_cached_metadata(
            query_key=query_key,
            provider=result.provider,
            payload_json=json.dumps(payload, ensure_ascii=False),
            ttl_days=self.ttl_days,
        )

    def _score_result(self, result: ProviderResult, title: str, author: str) -> ProviderResult:
        return score_candidate(result, title, author)

    def _lookup_isbn(self, isbn: str) -> ProviderResult | None:
        key = f"v2:isbn:{isbn}"
        cached = self._cache_get(key)
        if cached:
            return cached

        for provider in self.providers:
            for _ in range(self.retry_count):
                result = provider.lookup_by_isbn(isbn)
                if result and not result.is_empty and normalize_isbn(result.isbn) == isbn:
                    result.match_score = 1.0
                    self._cache_set(key, result)
                    return result
                time.sleep(0.1)
        return None

    def _lookup_title_author(self, title: str, author: str) -> ProviderResult | None:
        key = "v2:title_author:" + json.dumps([title.casefold(), author.casefold()], ensure_ascii=False)
        cached = self._cache_get(key)
        if cached and self._score_result(cached, title, author).match_score >= self.min_match_score:
            return cached

        candidates: list[ProviderResult] = []
        for provider in self.providers:
            for _ in range(self.retry_count):
                result = provider.lookup_by_title_author(title, author)
                if result and not result.is_empty:
                    candidates.append(self._score_result(result, title, author))
                    break
                time.sleep(0.1)

        if not candidates:
            return None
        best = sorted(
            candidates,
            key=lambda item: (item.match_score, item.confidence),
            reverse=True,
        )[0]
        if best.match_score >= self.min_match_score:
            self._cache_set(key, best)
            return best
        return None

    def _merge(self, raw: dict[str, Any], external: ProviderResult | None) -> dict[str, Any]:
        raw_title = normalize_spaces(str(raw.get("title", "")))
        raw_author = normalize_spaces(str(raw.get("author", "")))
        raw_isbn = normalize_isbn(str(raw.get("isbn", ""))) or ""
        series_hint = normalize_spaces(str(raw.get("series_hint", "")))

        if not external:
            return {
                "title": raw_title or "Неизвестное название",
                "author": raw_author or "Неизвестный автор",
                "isbn": raw_isbn,
                "series": series_hint,
                "series_index": "",
                "metadata_source": "local",
                "metadata_confidence": float(raw.get("confidence", 0.0) or 0.0),
                "provider_match_score": 0.0,
            }

        title = raw_title
        author = raw_author

        if not title or is_unknown_label(title):
            title = external.title or raw_title
        if not author or is_unknown_label(author):
            author = external.author or raw_author

        isbn_value = raw_isbn or normalize_isbn(external.isbn)
        series_value = normalize_spaces(external.series) or series_hint

        return {
            "title": title or "Неизвестное название",
            "author": author or "Неизвестный автор",
            "isbn": isbn_value or "",
            "series": series_value,
            "series_index": normalize_spaces(external.series_index),
            "metadata_source": external.provider,
            "metadata_confidence": max(float(raw.get("confidence", 0.0) or 0.0), external.confidence),
            "provider_match_score": external.match_score,
        }

    def enrich(self, raw: dict[str, Any]) -> dict[str, Any]:
        if not self.enabled:
            return self._merge(raw, None)

        isbn = normalize_isbn(str(raw.get("isbn", "")))
        title = normalize_spaces(str(raw.get("title", "")))
        author = normalize_spaces(str(raw.get("author", "")))
        if is_unknown_label(author):
            author = ""

        external: ProviderResult | None = None
        if isbn:
            external = self._lookup_isbn(isbn)

        if not external and title and not is_unknown_label(title):
            external = self._lookup_title_author(title, author)

        return self._merge(raw, external)
