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
    monkeypatch.setattr(main, "classify_category", lambda *a, **k: {"category": "Художественные | Детская", "confidence": 0.9})
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
