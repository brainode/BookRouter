# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 The ScanBookShelf Authors

from pathlib import Path

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
