# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 The ScanBookShelf Authors

from .base import MetadataProvider, ProviderResult
from .google_books import GoogleBooksProvider
from .openlibrary import OpenLibraryProvider

__all__ = [
    "MetadataProvider",
    "ProviderResult",
    "OpenLibraryProvider",
    "GoogleBooksProvider",
]
