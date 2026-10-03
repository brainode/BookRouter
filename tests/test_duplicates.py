# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 The ScanBookShelf Authors

from utils import file_fingerprint, find_duplicate


def test_matching_edges_do_not_make_different_books_duplicates(tmp_path):
    first = tmp_path / "first.pdf"
    second = tmp_path / "second.pdf"
    identical = tmp_path / "identical.pdf"
    first.write_bytes(b"head" + b"AAAA" + b"tail")
    second.write_bytes(b"head" + b"BBBB" + b"tail")
    identical.write_bytes(second.read_bytes())
    fingerprint = file_fingerprint(str(first), edge_bytes=4)
    assert file_fingerprint(str(second), edge_bytes=4) == fingerprint
    known = {fingerprint: [str(first)]}
    assert find_duplicate(str(second), fingerprint, known) is None
    known[fingerprint].append(str(second))
    assert find_duplicate(str(identical), fingerprint, known) == str(second)


def test_missing_candidate_is_skipped(tmp_path):
    source = tmp_path / "book.pdf"
    source.write_bytes(b"book")
    fingerprint = file_fingerprint(str(source))
    assert find_duplicate(str(source), fingerprint, {fingerprint: [str(tmp_path / "missing.pdf"), str(source)]}) == str(source)
