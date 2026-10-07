import importlib.util
from pathlib import Path

import pytest

from llm import LLMUnavailableError, build_category_path
from normalization import canonical_author_name, token_key
from sources import BookSource

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def main():
    spec = importlib.util.spec_from_file_location("bookrouter_main_pipeline", ROOT / "__main__.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _NoopEnricher:
    def enrich(self, facts):
        return {"title": facts["title"], "author": facts["author"], "isbn": "", "series": facts.get("series_hint", "")}


def _source(tmp_path, name="Иван Петров - Тыква [Весёлые книжки].pdf"):
    path = tmp_path / name
    path.write_bytes(b"%PDF-1.4")
    return BookSource(display_name=name, materialized_path=str(path), logical_path=str(path), origin_type="file", source_root=str(tmp_path))


def _options(main, tmp_path, dry_run=False):
    return main.RunOptions(input_folder=str(tmp_path), output_folder=str(tmp_path / "out"), dry_run=dry_run)


def test_canonical_author_name():
    assert canonical_author_name("Иван Иванович Петров") == "Иван Петров"
    assert canonical_author_name("John Q. Public") == "John Public"
    assert canonical_author_name("Джон Доу") == "Джон Доу"
    assert token_key("Петров Иван") == token_key("Иван Петров")
    assert build_category_path("Художественные | Детская", "Иван Иванович Петров", "").endswith("ivan-petrov | bez-serii")


def test_transient_llm_error_is_not_copied_to_errors(main, tmp_path, monkeypatch):
    def unavailable(*args, **kwargs):
        raise LLMUnavailableError("ollama_unavailable:down")

    monkeypatch.setattr(main, "extract_book_facts", unavailable)
    source = _source(tmp_path, "book.pdf")
    extracted = {"status": "ok", "index": 1, "source": source, "isbn": "", "text": "text", "error": "", "content_hash": "h"}
    result = main.process_file(extracted, _NoopEnricher(), _options(main, tmp_path))
    assert result["status"] == "error_transient"
    assert not (tmp_path / "out").exists()


def test_no_text_book_uses_filename(main, tmp_path, monkeypatch):
    monkeypatch.setattr(main, "decide_category", lambda *a, **k: {"category": "Художественные | Детская", "confidence": 0.9})
    source = _source(tmp_path)
    extracted = {"status": "error_no_text", "index": 1, "source": source, "isbn": "", "text": "", "error": "no_text_extracted", "content_hash": "h"}
    result = main.process_file(extracted, _NoopEnricher(), _options(main, tmp_path, dry_run=True))
    assert result["status"] == "ok"
    assert (result["title"], result["author"], result["series"]) == ("Тыква", "Иван Петров", "Весёлые книжки")
    assert result["metadata_source"] == "filename"
    assert not (tmp_path / "out").exists()  # dry-run


def test_no_text_book_with_meaningless_name_goes_to_errors(main, tmp_path):
    source = _source(tmp_path, "scan0001.pdf")
    extracted = {"status": "error_no_text", "index": 1, "source": source, "isbn": "", "text": "", "error": "no_text_extracted", "content_hash": ""}
    result = main.process_file(extracted, _NoopEnricher(), _options(main, tmp_path))
    assert result["status"] == "error_no_text"
    assert (tmp_path / "out" / "Errors" / "file" / "scan0001.pdf").exists()


def test_duplicate_skips_processing(main, tmp_path):
    source = _source(tmp_path, "copy.pdf")
    extracted = {"status": "duplicate", "index": 1, "source": source, "isbn": "", "text": "", "error": "C:\\lib\\orig.pdf", "content_hash": "h"}
    result = main.process_file(extracted, _NoopEnricher(), _options(main, tmp_path))
    assert result["status"] == "duplicate"
    assert result["new_path"] == "C:\\lib\\orig.pdf"
    assert not (tmp_path / "out").exists()


def test_uncertain_book_is_queued_not_copied_to_errors(main, tmp_path, monkeypatch):
    monkeypatch.setattr(main, "decide_category", lambda *a, **k: {
        "category": "Требует внимания", "confidence": 0.2, "review_reason": "insufficient_category_evidence"
    })
    source = _source(tmp_path)
    extracted = {"status": "error_no_text", "index": 1, "source": source, "isbn": "", "text": "", "error": "", "content_hash": "h"}
    result = main.process_file(extracted, _NoopEnricher(), _options(main, tmp_path))
    assert result["status"] == "needs_review"
    assert result["error_reason"] == "insufficient_category_evidence"
    assert result["category_confidence"] == 0.2
    assert result["metadata_confidence"] == 0.0
    assert result["facts_confidence"] > result["category_confidence"]
    assert Path(result["new_path"]).parent.name == "Требует внимания"
    assert not (tmp_path / "out" / "Errors").exists()


def test_review_only_selection(main, tmp_path):
    from db import BookDB
    db = BookDB(str(tmp_path / "books.db"))
    try:
        source = _source(tmp_path)
        key = main._normalized_origin_key(source)
        db.conn.execute(
            "INSERT INTO books(time_added, original_filename, original_path, origin_type, origin_path, status, new_path, source_size, source_mtime_ns) "
            "VALUES ('now', 'book.pdf', ?, ?, ?, 'needs_review', ?, ?, ?)", (key[1], key[0], key[1], key[1], *main.source_signature(source))
        )
        db.conn.commit()
        options = _options(main, tmp_path)
        assert main._select_sources([source], db, options, main.logger)[0] == []
        options.review_only = True
        assert main._select_sources([source], db, options, main.logger)[0] == [source]
    finally:
        db.close()


@pytest.mark.parametrize("failure", ["database", "csv"])
def test_persistence_failure_returns_nonzero(main, tmp_path, monkeypatch, failure):
    monkeypatch.chdir(tmp_path)
    main.stop_event.clear()
    source = _source(tmp_path)
    monkeypatch.setattr(main, "setup_logging", lambda: main.logger)
    monkeypatch.setattr(main, "install_print_logging", lambda logger: None)
    monkeypatch.setattr(main.signal, "signal", lambda *args: None)
    monkeypatch.setattr(main, "collect_book_sources", lambda *a, **k: ([source], {}))
    monkeypatch.setattr(main, "run_preflight", lambda *args: [])
    monkeypatch.setattr(main, "warm_up_model", lambda: 0)
    monkeypatch.setattr(main, "iter_extracted_files", lambda *args: iter_generator())

    def iter_generator():
        yield {"source": source, "status": "ok", "index": 1}

    monkeypatch.setattr(main, "process_file", lambda *args: main._error_result(source, 1, "needs_review", "review"))

    def fail(*args, **kwargs):
        raise OSError("injected persistence failure")

    if failure == "database":
        monkeypatch.setattr(main, "_store_result", fail)
    else:
        monkeypatch.setattr(main.BookDB, "export_results", fail)
    try:
        with pytest.raises(SystemExit) as exc:
            main.main(["--input", str(tmp_path), "--output", str(tmp_path / "out")])
        assert exc.value.code == 1
    finally:
        main.stop_event.clear()


@pytest.mark.parametrize("change", ["none", "source", "output", "legacy"])
def test_skip_checks_source_and_output(main, tmp_path, change):
    from db import BookDB
    source = _source(tmp_path)
    output = tmp_path / "sorted.pdf"
    output.write_bytes(b"%PDF-1.4")
    key = main._normalized_origin_key(source)
    size, mtime = main.source_signature(source)
    db = BookDB(str(tmp_path / "books.db"))
    try:
        db.conn.execute(
            "INSERT INTO books(time_added, original_filename, original_path, origin_type, origin_path, status, new_path, source_size, source_mtime_ns) "
            "VALUES ('now', 'book.pdf', ?, ?, ?, 'ok', ?, ?, ?)",
            (key[1], key[0], key[1], str(output), None if change == "legacy" else size, mtime),
        )
        db.conn.commit()
        if change == "source":
            Path(source.logical_path).write_bytes(b"replacement PDF")
        elif change == "output":
            output.unlink()
        selected, skipped = main._select_sources([source], db, _options(main, tmp_path), main.logger)
        assert selected == ([] if change == "none" else [source])
        assert skipped == (1 if change == "none" else 0)
    finally:
        db.close()


def test_zip_signature_tracks_archive(main, tmp_path):
    archive = tmp_path / "books.zip"
    archive.write_bytes(b"archive")
    source = BookSource(display_name="book.pdf", materialized_path="", origin_type="zip", archive_path=str(archive), logical_path="zip://books.zip!book.pdf")
    before = main.source_signature(source)
    archive.write_bytes(b"changed archive")
    assert main.source_signature(source) != before


def test_process_file_uses_author_slug(main, tmp_path, monkeypatch):
    from authors import add_alias, create_author
    from db import BookDB

    monkeypatch.setattr(main, "decide_category", lambda *a, **k: {"category": "Художественные | Детская", "confidence": 0.9})
    db = BookDB(str(tmp_path / "t.db"))
    author = create_author(db, "Иван Петров", slug="custom-slug")
    add_alias(db, author["id"], "Иван Петров")
    source = _source(tmp_path)
    extracted = {"status": "error_no_text", "index": 1, "source": source, "isbn": "", "text": "", "error": "no_text_extracted", "content_hash": "h"}
    result = main.process_file(extracted, _NoopEnricher(), _options(main, tmp_path), db=db)
    assert "custom-slug" in result["category"]
    assert result["author_id"] == author["id"]
