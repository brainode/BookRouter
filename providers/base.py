# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 The ScanBookShelf Authors

from dataclasses import dataclass, field
from typing import Any


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
