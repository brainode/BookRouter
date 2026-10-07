# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 The ScanBookShelf Authors

from dataclasses import dataclass, field
from typing import Any
from difflib import SequenceMatcher

from normalization import normalize_for_match


@dataclass
class ProviderResult:
    provider: str
    title: str = ""
    author: str = ""
    isbn: str = ""
    series: str = ""
    series_index: str = ""
    confidence: float = 0.0
    match_score: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def is_empty(self) -> bool:
        return not any([self.title, self.author, self.isbn, self.series])


class MetadataProvider:
    name = "base"

    def lookup_by_isbn(self, isbn: str) -> ProviderResult | None:
        raise NotImplementedError

    def lookup_by_title_author(self, title: str, author: str) -> ProviderResult | None:
        raise NotImplementedError


def score_candidate(result: ProviderResult, title: str, author: str) -> ProviderResult:
    def ratio(left, right):
        def key(value):
            value = value.casefold().replace("c++", "cplusplus").replace("c#", "csharp")
            return normalize_for_match(value)
        left, right = key(left), key(right)
        return SequenceMatcher(None, left, right).ratio() if left and right else 0.0
    title_score = ratio(title, result.title)
    result.match_score = round(title_score * 0.7 + ratio(author, result.author) * 0.3 if author else title_score, 4)
    return result


def best_candidate(results: list[ProviderResult], title: str, author: str) -> ProviderResult | None:
    candidates = [score_candidate(result, title, author) for result in results if not result.is_empty]
    return max(candidates, key=lambda result: (result.match_score, result.confidence), default=None)
