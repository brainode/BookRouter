import os
import zipfile

import pytest

import reader
from reader import ExtractError, _head_tail_ranges, detect_format, extract_text_with_ends, prepare_book_path


def _write(path, data: bytes):
    path.write_bytes(data)
    return str(path)


def _make_epub(path, chapters: list[str], extra_manifest: str = ""):
    """EPUB с OPF/spine; extra_manifest — ссылки на файлы, которых нет в архиве (битый манифест)."""
    items = "".join(
        f'<item id="c{i}" href="text/c{i}.xhtml" media-type="application/xhtml+xml"/>' for i in range(len(chapters))
    )
    spine = "".join(f'<itemref idref="c{i}"/>' for i in range(len(chapters)))
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            '<rootfiles><rootfile full-path="OEBPS/content.opf"/></rootfiles></container>',
        )
        archive.writestr(
            "OEBPS/content.opf",
            '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf">'
            f"<manifest>{items}{extra_manifest}</manifest><spine>{spine}</spine></package>",
        )
        for i, body in enumerate(chapters):
            archive.writestr(f"OEBPS/text/c{i}.xhtml", f"<html><body><p>{body}</p></body></html>")
    return str(path)


def test_head_tail_ranges_do_not_overlap():
    assert _head_tail_ranges(100, 8, 4) == (list(range(8)), [96, 97, 98, 99])
    assert _head_tail_ranges(5, 8, 4) == ([0, 1, 2, 3, 4], [])
    assert _head_tail_ranges(10, 8, 4) == (list(range(8)), [8, 9])


def test_detect_format_by_signature(tmp_path):
    assert detect_format(_write(tmp_path / "book.epub", b"%PDF-1.6\n...")) == "pdf"
    assert detect_format(_write(tmp_path / "book.djv", b"AT&TFORM\x00\x00")) == "djvu"
    assert detect_format(_write(tmp_path / "book.fb2", b'\xef\xbb\xbf<?xml version="1.0"?><FictionBook>')) == "fb2"
    assert detect_format(_make_epub(tmp_path / "real.epub", ["x"])) == "epub"
    with pytest.raises(ExtractError):
        detect_format(_write(tmp_path / "junk.epub", b"not a book at all"))


def test_epub_with_missing_manifest_item(tmp_path):
    path = _make_epub(
        tmp_path / "broken.epub",
        ["Первая глава книги", "Вторая глава книги"],
        extra_manifest='<item id="img" href="media/2.png" media-type="image/png"/><itemref idref="img"/>',
    )
    head, tail = extract_text_with_ends(path, 1, 0)
    assert head == "Первая глава книги Вторая глава книги"
    assert tail == ""


def test_epub_tail_keeps_reading_order(tmp_path, monkeypatch):
    monkeypatch.setattr(reader, "WORDS_PER_PAGES", 2)
    path = _make_epub(tmp_path / "book.epub", ["a1 a2", "b1 b2", "c1 c2"])
    head, tail = extract_text_with_ends(path, 1, 1)
    assert head == "a1 a2"
    assert tail == "c1 c2"


def test_fb2_excludes_binary_and_keeps_description(tmp_path):
    fb2 = (
        '<?xml version="1.0" encoding="windows-1251"?>'
        '<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">'
        "<description><title-info><book-title>Тест</book-title></title-info>"
        "<publish-info><isbn>978-0-306-40615-7</isbn></publish-info></description>"
        "<body><p>Текст книги</p></body>"
        '<binary id="cover.jpg">QUJDREVGR0g=</binary>'
        "</FictionBook>"
    ).encode("windows-1251")
    head, tail = extract_text_with_ends(_write(tmp_path / "book.fb2", fb2), 1, 1)
    assert "978-0-306-40615-7" in head
    assert "Текст книги" in head
    assert "QUJDREVGR0g" not in head + tail


def test_fb2_invalid_xml_falls_back(tmp_path):
    broken = '<?xml version="1.0"?><FictionBook><body><p>Текст & без экранирования</p></body><binary>QUJD</binary>'.encode()
    head, _ = extract_text_with_ends(_write(tmp_path / "broken.fb2", broken), 1, 0)
    assert "Текст" in head
    assert "QUJD" not in head


def test_fb2_inside_zip(tmp_path):
    path = tmp_path / "book.fb2"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("inner.fb2", '<?xml version="1.0" encoding="utf-8"?><FictionBook><body><p>Внутри zip</p></body></FictionBook>')
    head, _ = extract_text_with_ends(str(path), 1, 0)
    assert head == "Внутри zip"


def test_prepare_book_path_copies_when_directory_is_not_ascii(tmp_path):
    folder = tmp_path / "Папка с кириллицей"
    folder.mkdir()
    original = _write(folder / "book.djv", b"AT&TFORM")
    safe_path, temp_dir = prepare_book_path(original)
    try:
        assert temp_dir is not None
        assert safe_path.isascii() or not str(tmp_path).isascii()
        assert os.path.basename(safe_path) == "book.djv"
        with open(safe_path, "rb") as f:
            assert f.read() == b"AT&TFORM"
    finally:
        temp_dir.cleanup()


def test_prepare_book_path_keeps_ascii_path(tmp_path):
    if not str(tmp_path).isascii():
        pytest.skip("временная папка содержит не-ASCII символы")
    original = _write(tmp_path / "book.djvu", b"AT&TFORM")
    assert prepare_book_path(original) == (original, None)


def test_ocr_timeout_on_one_page_skips_it():
    def ocr_page(page):
        if page == 1:
            raise reader.OCRTimeoutError("ocr_timeout>45s")
        return f"page{page}"

    assert reader._ocr_pages([0, 1, 2], "head", ocr_page) == "page0\npage2"


def test_ocr_timeout_on_all_pages_fails_book():
    def ocr_page(page):
        raise reader.OCRTimeoutError("ocr_timeout>45s")

    with pytest.raises(reader.OCRTimeoutError):
        reader._ocr_pages([0, 1], "head", ocr_page)


def test_fb2_metadata_from_description(tmp_path):
    path = tmp_path / "b.fb2"
    path.write_text(
        '<?xml version="1.0" encoding="utf-8"?><FictionBook><description><title-info>'
        "<genre>sf_horror</genre><genre>sf</genre><author><first-name>Стивен</first-name>"
        "<last-name>Кинг</last-name></author><book-title>Оно</book-title></title-info>"
        "<publish-info><publisher>АСТ</publisher><year>2015 г.</year><isbn>978-5-17-089524-8</isbn>"
        "</publish-info></description><body><section><p>текст книги</p></section></body></FictionBook>",
        encoding="utf-8",
    )
    result = reader.extract_book(str(path))
    assert result.fmt == "fb2"
    assert result.embedded["genres"] == ["sf_horror", "sf"]
    assert result.embedded["author"] == "Стивен Кинг"
    assert result.embedded["year"] == "2015"
    assert result.embedded["publisher"] == "АСТ"
    assert result.embedded["title"] == "Оно"


def test_epub_metadata_from_opf(tmp_path):
    path = tmp_path / "b.epub"
    container = (
        '<?xml version="1.0"?><container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
        '<rootfiles><rootfile full-path="content.opf" media-type="application/oebps-package+xml"/>'
        "</rootfiles></container>"
    )
    opf = (
        '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="2.0">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Книга</dc:title>'
        "<dc:creator>Автор</dc:creator><dc:date>2019-05-01</dc:date><dc:publisher>Изд</dc:publisher>"
        "<dc:subject>Фантастика</dc:subject><dc:subject>Ужасы</dc:subject>"
        "<dc:identifier>urn:isbn:978-5-17-089524-3</dc:identifier></metadata>"
        '<manifest><item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/></manifest>'
        '<spine><itemref idref="c1"/></spine></package>'
    )
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("META-INF/container.xml", container)
        z.writestr("content.opf", opf)
        z.writestr("c1.xhtml", "<html><body><p>hello world</p></body></html>")
    emb = reader.extract_book(str(path)).embedded
    assert emb["year"] == "2019"
    assert emb["subjects"] == ["Фантастика", "Ужасы"]
    assert emb["isbn"] == "9785170895243"
    assert emb["publisher"] == "Изд"


def test_quality_score_order():
    q = reader.quality_score
    assert q("pdf", False) > q("djvu", False) > q("pdf", True)
    assert q("epub", False) == 300
