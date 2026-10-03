# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 The ScanBookShelf Authors

from pathlib import Path

import pytest

import utils

from utils import copy_book_to_category


def test_fixed_categories_do_not_merge_cpp_and_csharp(tmp_path):
    source = tmp_path / "book.pdf"
    source.write_bytes(b"book")
    output = tmp_path / "out"
    cpp = copy_book_to_category(str(source), "CPP", "Author", "IT | Языки программирования | C++", str(output))
    csharp = copy_book_to_category(str(source), "CS", "Author", "IT | Языки программирования | C#", str(output))
    assert Path(cpp).parent.name == "C++"
    assert Path(csharp).parent.name == "C#"
    assert Path(csharp).read_bytes() == b"book"


def test_fiction_author_variants_still_reuse_folder(tmp_path):
    source = tmp_path / "book.pdf"
    source.write_bytes(b"book")
    output = tmp_path / "out"
    author_folder = output / "Художественные" / "Детская" / "petrov-ivan"
    author_folder.mkdir(parents=True)
    result = copy_book_to_category(str(source), "Book", "Author", "Художественные | Детская | ivan-petrov | bez-serii", str(output))
    assert Path(result).parent.parent == author_folder


def test_copy_retry_reuses_complete_identical_file(tmp_path):
    source = tmp_path / "book.pdf"
    source.write_bytes(b"book")
    output = tmp_path / "out"
    first = copy_book_to_category(str(source), "Book", "Author", "IT | Data Science", str(output))
    second = copy_book_to_category(str(source), "Book", "Author", "IT | Data Science", str(output))
    assert first == second
    assert len(list(Path(first).parent.iterdir())) == 1
    source.write_bytes(b"diff")
    third = copy_book_to_category(str(source), "Book", "Author", "IT | Data Science", str(output))
    assert third != first
    assert Path(first).read_bytes() == b"book"


def test_failed_copy_never_publishes_partial_file(tmp_path, monkeypatch):
    source = tmp_path / "book.pdf"
    source.write_bytes(b"book")
    destination = tmp_path / "out" / "book.pdf"

    def fail(src, dst):
        Path(dst).write_bytes(b"partial")
        raise OSError("disk full")

    monkeypatch.setattr(utils.shutil, "copy2", fail)
    with pytest.raises(OSError, match="disk full"):
        utils.atomic_copy(str(source), str(destination))
    assert list(destination.parent.iterdir()) == []


def test_failed_csv_export_keeps_previous_snapshot(tmp_path):
    path = tmp_path / "results.csv"
    path.write_text("previous", encoding="utf-8")

    def rows():
        yield ["partial"]
        raise OSError("failed to read database")

    with pytest.raises(OSError):
        utils.write_results_csv(str(path), rows())
    assert path.read_text(encoding="utf-8") == "previous"
    assert list(tmp_path.iterdir()) == [path]
