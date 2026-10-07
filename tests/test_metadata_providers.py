# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 The ScanBookShelf Authors

import pytest

from metadata_enricher import MetadataEnricher
from providers.base import ProviderResult
from providers.google_books import GoogleBooksProvider
from providers.openlibrary import OpenLibraryProvider

ISBN = "9780306406157"
OTHER_ISBN = "9780131103627"


def _google_item(title, author="Author", isbn=ISBN):
    return {"volumeInfo": {"title": title, "authors": [author], "industryIdentifiers": [{"type": "ISBN_13", "identifier": isbn}]}}


@pytest.mark.parametrize("provider_type", [GoogleBooksProvider, OpenLibraryProvider])
def test_title_lookup_selects_best_candidate_not_first(monkeypatch, provider_type):
    provider = provider_type()
    if provider_type is GoogleBooksProvider:
        response = {"items": [_google_item("Unrelated"), _google_item("Target") ]}
    else:
        response = {"docs": [{"title": "Unrelated", "author_name": ["Author"], "isbn": [ISBN]},
                             {"title": "Target", "author_name": ["Author"], "isbn": [ISBN, OTHER_ISBN]}]}
    monkeypatch.setattr(provider, "_get_json", lambda url: response)
    result = provider.lookup_by_title_author("Target", "Author")
    assert result.title == "Target"
    assert result.match_score == 1.0
    assert result.isbn == ""  # Work-level match does not identify the edition.


def test_google_isbn_lookup_requires_matching_identifier(monkeypatch):
    provider = GoogleBooksProvider()
    response = {"items": [_google_item("Wrong edition", isbn=OTHER_ISBN), _google_item("Correct edition")]}
    monkeypatch.setattr(provider, "_get_json", lambda url: response)
    assert provider.lookup_by_isbn(ISBN).title == "Correct edition"
    response["items"].pop()
    assert provider.lookup_by_isbn(ISBN) is None


def test_google_accepts_equivalent_isbn10(monkeypatch):
    provider = GoogleBooksProvider()
    item = _google_item("Target")
    item["volumeInfo"]["industryIdentifiers"] = [{"type": "ISBN_10", "identifier": "0306406152"}]
    monkeypatch.setattr(provider, "_get_json", lambda url: {"items": [item]})
    assert provider.lookup_by_isbn(ISBN).isbn == ISBN


def test_openlibrary_rejects_different_edition(monkeypatch):
    provider = OpenLibraryProvider()
    monkeypatch.setattr(provider, "_get_json", lambda url: {"title": "Wrong edition", "isbn_13": [OTHER_ISBN]})
    assert provider.lookup_by_isbn(ISBN) is None


def test_external_isbn_does_not_replace_original():
    enricher = MetadataEnricher()
    result = enricher._merge({"title": "Title", "author": "Author", "isbn": ISBN},
                             ProviderResult(provider="fake", isbn=OTHER_ISBN))
    assert result["isbn"] == ISBN


def test_high_provider_confidence_cannot_override_poor_match(monkeypatch):
    provider = GoogleBooksProvider()
    monkeypatch.setattr(provider, "lookup_by_title_author", lambda *args: ProviderResult(
        provider="fake", title="Unrelated", author="Someone else", confidence=0.99
    ))
    enricher = MetadataEnricher()
    enricher.providers = [provider]
    enricher.retry_count = 1
    assert enricher._lookup_title_author("Target", "Author") is None


def test_exact_title_can_match_without_author(monkeypatch):
    provider = GoogleBooksProvider()
    monkeypatch.setattr(provider, "_get_json", lambda url: {"items": [_google_item("Target")]})
    enricher = MetadataEnricher()
    enricher.providers = [provider]
    assert enricher._lookup_title_author("Target", "").match_score == 1.0


def test_cpp_and_csharp_candidates_are_distinct(monkeypatch):
    provider = GoogleBooksProvider()
    monkeypatch.setattr(provider, "_get_json", lambda url: {"items": [_google_item("Learn C++"), _google_item("Learn C#")]})
    assert provider.lookup_by_title_author("Learn C#", "Author").title == "Learn C#"
