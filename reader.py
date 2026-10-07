# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 The ScanBookShelf Authors

import io
import os
import posixpath
import re
import shutil
import subprocess
import warnings
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from tempfile import TemporaryDirectory
from urllib.parse import unquote

import pymupdf
import pytesseract
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
from PIL import Image

# Книги — локальные файлы пользователя, а не загрузки из сети: защита от «бомб» PIL мешает огромным сканам
Image.MAX_IMAGE_PIXELS = 400_000_000
# Больше этого Tesseract не нужен: страница рендерится с меньшим dpi
MAX_OCR_PIXELS = 60_000_000

from config import (
    DDJVU_PAGE_TIMEOUT_SEC,
    LANGUAGES,
    MAX_PAGES,
    MAX_TAIL_PAGES,
    OCR_DPI,
    OCR_ENABLED,
    OCR_PAGE_TIMEOUT_SEC,
    TESSERACT_CMD,
    WORDS_PER_PAGES,
)
from interrupt import Interrupted, check_interrupted, stop_event
from normalization import extract_first_valid_isbn, normalize_isbn
from utils import long_path


def resolve_tesseract_cmd(raw: str) -> str:
    """TESSERACT_CMD может указывать на папку установки — дописываем имя исполняемого файла."""
    value = str(raw or "").strip()
    if value and os.path.isdir(value):
        value = os.path.join(value, "tesseract.exe" if os.name == "nt" else "tesseract")
    return value


# xhtml глав EPUB и битые fb2 намеренно читаются терпимым html.parser
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

TESSERACT_PATH = resolve_tesseract_cmd(TESSERACT_CMD)
if OCR_ENABLED and TESSERACT_PATH:
    pytesseract.pytesseract.tesseract_cmd = TESSERACT_PATH

# Отдельная группа процессов: Ctrl+C в консоли не убивает djvused/ddjvu посреди страницы,
# остановка идёт через stop_event между страницами.
_SUBPROCESS_FLAGS = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)


class OCRTimeoutError(RuntimeError):
    """Таймаут OCR/DDJVU для маршрутизации файла в Errors."""


class OCRConfigError(RuntimeError):
    """Внешний инструмент (tesseract, DjVuLibre) не запускается — ошибка окружения, а не книги."""


class ExtractError(RuntimeError):
    """Книгу не удалось прочитать; сообщение идёт в error_reason."""


@dataclass
class ExtractedText:
    head: str
    tail: str
    fmt: str
    page_count: int = 0          # страницы PDF/DjVu; для EPUB/FB2 — 0
    ocr_used: bool = False       # начало книги распознавалось OCR — текстового слоя нет
    embedded: dict = field(default_factory=dict)  # title, author, year, publisher, isbn, genres, subjects


QUALITY_BY_KIND = {"pdf": 300, "epub": 300, "fb2": 250, "djvu": 200, "ocr": 100}


def quality_score(fmt: str, ocr_used: bool) -> int:
    """Чем больше, тем лучше экземпляр: родной текст лучше скана с текстовым слоем, тот — лучше OCR."""
    if ocr_used and fmt in ("pdf", "djvu"):
        return QUALITY_BY_KIND["ocr"]
    return QUALITY_BY_KIND.get(fmt, 0)


def _is_timeout_message(message) -> bool:
    lowered = str(message or "").lower()
    return "timeout" in lowered or "timed out" in lowered


def prepare_book_path(path):
    """
    Консольные утилиты DjVuLibre под Windows не открывают пути с не-ASCII символами
    (в имени файла или в любом каталоге). Такой файл копируется во временную папку
    с транслитерированным именем.
    Возвращает (новый_путь, temp_dir_obj); temp_dir_obj нужно держать, пока файл используется.
    """

    # Транслитерация кириллицы в латиницу (ГОСТ упрощённый)
    translit_map = {
        'а':'a', 'б':'b', 'в':'v', 'г':'g', 'д':'d', 'е':'e', 'ё':'yo', 'ж':'zh', 'з':'z',
        'и':'i', 'й':'y', 'к':'k', 'л':'l', 'м':'m', 'н':'n', 'о':'o', 'п':'p', 'р':'r',
        'с':'s', 'т':'t', 'у':'u', 'ф':'f', 'х':'h', 'ц':'ts', 'ч':'ch', 'ш':'sh', 'щ':'sch',
        'ъ':'',  'ы':'y', 'ь':'',  'э':'e', 'ю':'yu', 'я':'ya'
    }
    translit_map.update({k.upper(): v.capitalize() for k, v in translit_map.items()})

    def transliterate(text):
        return ''.join(translit_map.get(ch, ch) for ch in text)

    if str(path).isascii() and len(os.path.abspath(path)) < 240:
        return path, None

    temp_dir = TemporaryDirectory()
    safe_name = transliterate(os.path.basename(path))
    safe_name = re.sub(r'[^A-Za-z0-9._-]', '_', safe_name) or "book.djvu"
    new_path = os.path.join(temp_dir.name, safe_name)
    shutil.copy2(long_path(path), new_path)
    return new_path, temp_dir


def needs_ocr(text):
    """Определяет, нужно ли применять OCR к документу"""
    # Если текст не удалось извлечь или его слишком мало - нужен OCR
    if not text or len(text.split()) < 50:
        return True
    return False


def ocr_image(img) -> str:
    """Распознаёт одно изображение страницы. Ошибки окружения пробрасываются, а не превращаются в пустой текст."""
    check_interrupted()
    timeout_sec = max(1, int(OCR_PAGE_TIMEOUT_SEC))
    try:
        return pytesseract.image_to_string(img, lang=LANGUAGES, timeout=timeout_sec)
    except pytesseract.TesseractNotFoundError as exc:
        raise OCRConfigError(f"tesseract_not_found:{pytesseract.pytesseract.tesseract_cmd}") from exc
    except OSError as exc:
        # PermissionError [WinError 5] — TESSERACT_CMD указывает не на исполняемый файл
        raise OCRConfigError(f"tesseract_not_runnable:{exc}") from exc
    except RuntimeError as exc:
        # Ctrl+C убивает дочерний tesseract — это прерывание, а не ошибка книги
        if stop_event.is_set():
            raise Interrupted("processing_interrupted") from exc
        if _is_timeout_message(exc):
            raise OCRTimeoutError(f"ocr_timeout>{timeout_sec}s") from exc
        raise ExtractError(f"ocr_failed:{exc}") from exc


def _ocr_dpi_for_page(width_pt: float, height_pt: float) -> int:
    """dpi рендера страницы PDF: OCR_DPI, но не больше MAX_OCR_PIXELS пикселей (нижняя граница 10 dpi)."""
    area_in2 = max(width_pt, 1.0) * max(height_pt, 1.0) / (72 * 72)
    limit = int((MAX_OCR_PIXELS / area_in2) ** 0.5)
    return max(10, min(OCR_DPI, limit))


def perform_ocr_on_page(page):
    """Выполняет OCR на одной странице PDF"""
    pix = page.get_pixmap(dpi=_ocr_dpi_for_page(page.rect.width, page.rect.height))
    with Image.open(io.BytesIO(pix.tobytes(output="png"))) as img:
        return ocr_image(img)


def _head_tail_ranges(total: int, head_pages: int, tail_pages: int) -> tuple[list[int], list[int]]:
    """0-based номера страниц начала и конца; хвост не пересекается с началом."""
    head = list(range(min(max(0, head_pages), total)))
    tail = list(range(max(len(head), total - max(0, tail_pages)), total))
    return head, tail


def detect_format(path) -> str:
    """Формат по сигнатуре файла: расширению доверять нельзя (встречаются PDF с расширением .epub)."""
    with open(long_path(path), "rb") as f:
        header = f.read(1024)

    if header.startswith(b"AT&TFORM"):
        return "djvu"
    if header.startswith(b"PK\x03\x04"):
        if os.path.splitext(path)[1].lower() == ".fb2":
            return "fb2"  # fb2, упакованный в zip
        return "epub"
    if b"%PDF" in header:
        return "pdf"
    stripped = header.lstrip(b"\xef\xbb\xbf \t\r\n")
    if stripped.startswith(b"<") and b"FictionBook" in header:
        return "fb2"
    if stripped.startswith(b"<") and os.path.splitext(path)[1].lower() == ".fb2":
        return "fb2"
    raise ExtractError(f"unknown_format:{header[:8]!r}")


def extract_book(path, head_pages=MAX_PAGES, tail_pages=MAX_TAIL_PAGES) -> ExtractedText:
    """
    Извлекает текст из первых head_pages и последних tail_pages страниц.
    Для fb2/epub страницы считаются как блоки по WORDS_PER_PAGES слов.
    """
    print(f"📄 Извлечение текста из: {os.path.basename(path)}")

    fmt = detect_format(path)
    stats: dict = {}
    func = {
        "pdf": extract_text_pdf,
        "djvu": extract_text_djvu,
        "epub": extract_text_epub,
    }.get(fmt, extract_text_fb2)
    head, tail = func(path, head_pages, tail_pages, stats=stats)
    return ExtractedText(
        head, tail, fmt, stats.get("page_count", 0), stats.get("ocr_used", False), stats.get("embedded", {})
    )


def probe_book(path, pages: int = 3) -> ExtractedText:
    """Дешёвая проверка без OCR: формат, число страниц, есть ли текстовый слой, встроенные метаданные."""
    fmt = detect_format(path)
    page_count, ocr_used, embedded = 0, False, {}
    if fmt == "pdf":
        with pymupdf.open(long_path(path), filetype="pdf") as doc:
            if doc.needs_pass:
                raise ExtractError("pdf_encrypted")
            page_count = len(doc)
            text = "\n".join(doc[i].get_text() for i in range(min(pages, page_count)))
        ocr_used = needs_ocr(text)
    elif fmt == "djvu":
        timeout_sec = max(1, int(DDJVU_PAGE_TIMEOUT_SEC))
        safe_path, temp_dir = prepare_book_path(path)
        try:
            raw = _run_tool(["djvused", "-e", "n", safe_path], timeout_sec).decode("ascii", errors="replace").strip()
            if not raw.isdigit():
                raise ExtractError(f"djvused_bad_page_count:{raw[:40]!r}")
            page_count = int(raw)
            text = "\n".join(
                _run_tool(["djvutxt", f"--page={p + 1}", safe_path], timeout_sec).decode("utf-8", errors="replace")
                for p in range(min(pages, page_count))
            )
            ocr_used = needs_ocr(text)
        finally:
            if temp_dir:
                temp_dir.cleanup()
    elif fmt == "epub":
        embedded = _epub_metadata(path)
    else:
        embedded = _fb2_metadata(_read_fb2_bytes(path))
    return ExtractedText("", "", fmt, page_count, ocr_used, embedded)


def extract_text_with_ends(path, head_pages=MAX_PAGES, tail_pages=MAX_TAIL_PAGES):
    """Возвращает (head, tail); см. extract_book."""
    result = extract_book(path, head_pages, tail_pages)
    return result.head, result.tail


def _tail_ocr_needed(head_text: str) -> bool:
    # Хвост читается только ради ISBN: если он уже найден в начале, дорогой OCR хвоста не нужен
    return not extract_first_valid_isbn(head_text)


def _ocr_pages(pages: list[int], label: str, ocr_page) -> str:
    """OCR диапазона страниц. Таймаут одной страницы её пропускает; ошибка — только если не удалась ни одна."""
    parts = []
    timeouts = []
    for done, page in enumerate(pages, start=1):
        try:
            parts.append(ocr_page(page))
            print(f"📄 OCR {label} страница {done}/{len(pages)}")
        except OCRTimeoutError as exc:
            timeouts.append(str(exc))
            print(f"⚠️ OCR {label} страница {done}/{len(pages)} пропущена: {exc}")
    if timeouts and len(timeouts) == len(pages):
        raise OCRTimeoutError(timeouts[0])
    return "\n".join(parts).strip()


def _pdf_pages_text(doc, pages: list[int], label: str, allow_ocr: bool = True, stats: dict | None = None) -> str:
    text = "\n".join(doc[page_number].get_text() for page_number in pages)
    if OCR_ENABLED and allow_ocr and pages and needs_ocr(text):
        if stats is not None and label == "head":
            stats["ocr_used"] = True
        print(f"🔍 Применяю OCR для PDF ({label})")
        return _ocr_pages(pages, label, lambda page_number: perform_ocr_on_page(doc[page_number]))
    return text.strip()


def extract_text_pdf(path, head_pages, tail_pages, stats=None):
    """Извлекает текст из PDF с поддержкой OCR"""
    # filetype явно: PyMuPDF иначе выбирает тип по расширению (а бывают PDF с расширением .epub)
    with pymupdf.open(long_path(path), filetype="pdf") as doc:
        if doc.needs_pass:
            raise ExtractError("pdf_encrypted")
        if stats is not None:
            stats["page_count"] = len(doc)
        head, tail = _head_tail_ranges(len(doc), head_pages, tail_pages)
        head_text = _pdf_pages_text(doc, head, "head", stats=stats)
        return head_text, _pdf_pages_text(doc, tail, "tail", allow_ocr=_tail_ocr_needed(head_text))


def _run_tool(args: list[str], timeout_sec: int) -> bytes:
    check_interrupted()
    tool = args[0]
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            timeout=timeout_sec,
            creationflags=_SUBPROCESS_FLAGS,
        )
    except FileNotFoundError as exc:
        raise OCRConfigError(f"{tool}_not_found: установи DjVuLibre и добавь его в PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise OCRTimeoutError(f"{tool}_timeout>{timeout_sec}s") from exc

    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", errors="replace").strip().splitlines()
        raise ExtractError(f"{tool}_failed rc={proc.returncode}: {stderr[0] if stderr else ''}")
    return proc.stdout


def _djvu_pages_text(
    safe_path: str, pages: list[int], label: str, tmpdir: str, timeout_sec: int, allow_ocr: bool = True,
    stats: dict | None = None,
) -> str:
    # Сначала текстовый слой: многие djvu уже распознаны, OCR тогда не нужен.
    layer = [
        _run_tool(["djvutxt", f"--page={page + 1}", safe_path], timeout_sec).decode("utf-8", errors="replace")
        for page in pages
    ]
    text = "\n".join(layer)
    if not OCR_ENABLED or not allow_ocr or not pages or not needs_ocr(text):
        return text.strip()

    def ocr_page(page: int) -> str:
        out_file = os.path.join(tmpdir, f"page{page + 1}.tiff")
        # Страницы у ddjvu нумеруются с 1. Сканы часто в 600 dpi: на таком растре Tesseract
        # тратит минуты на страницу и находит мусор в иллюстрациях, на OCR_DPI — секунды.
        _run_tool(["ddjvu", "-format=tiff", f"-scale={OCR_DPI}", f"-page={page + 1}", safe_path, out_file], timeout_sec)
        try:
            with Image.open(out_file) as img:
                return ocr_image(img)
        finally:
            os.remove(out_file)

    if stats is not None and label == "head":
        stats["ocr_used"] = True
    print(f"🔍 Применяю OCR для DJVU ({label})")
    return _ocr_pages(pages, label, ocr_page)


def extract_text_djvu(path, head_pages, tail_pages, stats=None):
    """Извлекает текст из DJVU: текстовый слой через djvutxt, иначе OCR страниц, отрендеренных ddjvu"""
    timeout_sec = max(1, int(DDJVU_PAGE_TIMEOUT_SEC))
    safe_path, temp_dir = prepare_book_path(path)
    try:
        raw_count = _run_tool(["djvused", "-e", "n", safe_path], timeout_sec).decode("ascii", errors="replace").strip()
        if not raw_count.isdigit():
            raise ExtractError(f"djvused_bad_page_count:{raw_count[:40]!r}")
        if stats is not None:
            stats["page_count"] = int(raw_count)
        head, tail = _head_tail_ranges(int(raw_count), head_pages, tail_pages)
        with TemporaryDirectory() as tmpdir:
            head_text = _djvu_pages_text(safe_path, head, "head", tmpdir, timeout_sec, stats=stats)
            tail_text = _djvu_pages_text(
                safe_path, tail, "tail", tmpdir, timeout_sec, allow_ocr=_tail_ocr_needed(head_text)
            )
            return head_text, tail_text
    finally:
        if temp_dir:
            temp_dir.cleanup()


def _local_name(tag) -> str:
    return str(tag).rsplit("}", 1)[-1]


def _split_head_tail_words(words: list[str], head_pages: int, tail_pages: int) -> tuple[str, str]:
    head = words[: WORDS_PER_PAGES * max(0, head_pages)]
    tail_start = max(len(head), len(words) - WORDS_PER_PAGES * max(0, tail_pages))
    return " ".join(head), " ".join(words[tail_start:])


def _epub_opf_path(archive: zipfile.ZipFile) -> str:
    names = archive.namelist()
    opf_path = ""
    if "META-INF/container.xml" in names:
        container = ET.fromstring(archive.read("META-INF/container.xml"))
        for elem in container.iter():
            if _local_name(elem.tag) == "rootfile" and elem.get("full-path"):
                opf_path = elem.get("full-path")
                break
    if not opf_path:
        opf_path = next((name for name in names if name.lower().endswith(".opf")), "")
    return opf_path


def _epub_spine_paths(archive: zipfile.ZipFile) -> list[str]:
    names = archive.namelist()
    opf_path = _epub_opf_path(archive)

    spine: list[str] = []
    if opf_path in names:
        opf = ET.fromstring(archive.read(opf_path))
        base = posixpath.dirname(opf_path)
        manifest = {}
        for elem in opf.iter():
            if _local_name(elem.tag) == "item" and elem.get("id") and elem.get("href"):
                href = unquote(elem.get("href").split("#", 1)[0])
                manifest[elem.get("id")] = posixpath.normpath(posixpath.join(base, href))
        for elem in opf.iter():
            if _local_name(elem.tag) == "itemref" and elem.get("idref") in manifest:
                spine.append(manifest[elem.get("idref")])

    if not spine:
        spine = sorted(name for name in names if name.lower().endswith((".xhtml", ".html", ".htm")))
    # Битый манифест ссылается на отсутствующие файлы — пропускаем их, а не падаем
    available = set(names)
    return [name for name in dict.fromkeys(spine) if name in available]


def _epub_words(path) -> list[str]:
    words: list[str] = []
    with zipfile.ZipFile(long_path(path)) as archive:
        for name in _epub_spine_paths(archive):
            soup = BeautifulSoup(archive.read(name), "html.parser")
            words.extend(soup.get_text(" ").split())
    return words


def _pymupdf_text(path, head_pages, tail_pages) -> tuple[str, str]:
    with pymupdf.open(long_path(path), filetype="epub") as doc:
        head, tail = _head_tail_ranges(len(doc), head_pages, tail_pages)
        return (
            "\n".join(doc[page].get_text() for page in head).strip(),
            "\n".join(doc[page].get_text() for page in tail).strip(),
        )


def _epub_metadata(path) -> dict:
    try:
        with zipfile.ZipFile(long_path(path)) as archive:
            opf_path = _epub_opf_path(archive)
            opf = ET.fromstring(archive.read(opf_path))
        meta = {"title": "", "author": "", "year": "", "publisher": "", "isbn": "", "genres": [], "subjects": []}
        metadata = next((e for e in opf.iter() if _local_name(e.tag) == "metadata"), None)
        if metadata is None:
            return {}
        values: dict[str, list[str]] = {}
        for e in metadata:
            text = " ".join("".join(e.itertext()).split())
            if text:
                values.setdefault(_local_name(e.tag), []).append(text)
        meta["title"] = (values.get("title") or [""])[0]
        meta["author"] = (values.get("creator") or [""])[0]
        meta["publisher"] = (values.get("publisher") or [""])[0]
        date = (values.get("date") or [""])[0]
        m = re.match(r"\d{4}", date)
        meta["year"] = m.group(0) if m else ""
        meta["subjects"] = values.get("subject", [])
        for ident in values.get("identifier", []):
            isbn = normalize_isbn(ident)
            if isbn:
                meta["isbn"] = isbn
                break
        return meta
    except Exception:
        return {}


def extract_text_epub(path, head_pages, tail_pages, stats=None):
    if stats is not None:
        stats["embedded"] = _epub_metadata(path)
    try:
        words = _epub_words(path)
    except Exception as exc:
        print(f"⚠️ EPUB не разобран напрямую ({exc}), пробую PyMuPDF")
        return _pymupdf_text(path, head_pages, tail_pages)
    return _split_head_tail_words(words, head_pages, tail_pages)


def _read_fb2_bytes(path) -> bytes:
    if zipfile.is_zipfile(long_path(path)):
        with zipfile.ZipFile(long_path(path)) as archive:
            member = next((name for name in archive.namelist() if name.lower().endswith(".fb2")), None)
            if not member:
                raise ExtractError("fb2_zip_without_fb2")
            return archive.read(member)
    with open(long_path(path), "rb") as f:
        return f.read()


def _fb2_metadata(data: bytes) -> dict:
    """Жанры, автор, издательство, год и ISBN из description FB2."""
    meta = {"title": "", "author": "", "year": "", "publisher": "", "isbn": "", "genres": [], "subjects": []}
    try:
        try:
            root = ET.fromstring(data)
            elems = list(root.iter())
            name = lambda e: _local_name(e.tag).lower()
            text = lambda e: " ".join("".join(e.itertext()).split())
            children = lambda e: list(e)
        except ET.ParseError:
            root = BeautifulSoup(data, "html.parser")
            elems = root.find_all(True)
            name = lambda e: str(e.name).lower()
            text = lambda e: " ".join(e.get_text(" ").split())
            children = lambda e: e.find_all(True, recursive=False)

        parents = {}
        for e in elems:
            for c in children(e):
                parents[id(c)] = e

        def _ancestors(e):
            out = []
            while id(e) in parents:
                e = parents[id(e)]
                out.append(e)
            return [name(a) for a in out]

        def in_section(e, section):
            return section in _ancestors(e)

        def first(section, tag):
            for e in elems:
                if name(e) == tag and in_section(e, section):
                    return e
            return None

        meta["genres"] = [
            text(e) for e in elems if name(e) == "genre" and in_section(e, "title-info") and text(e)
        ]
        title = first("title-info", "book-title")
        if title is not None:
            meta["title"] = text(title)
        author = first("title-info", "author")
        if author is not None:
            parts = []
            for tag in ("first-name", "middle-name", "last-name"):
                for c in children(author):
                    if name(c) == tag and text(c):
                        parts.append(text(c))
            meta["author"] = " ".join(parts)
        for tag in ("publisher", "year", "isbn"):
            e = first("publish-info", tag)
            if e is not None:
                meta[tag] = text(e)
        m = re.search(r"\d{4}", meta["year"])
        meta["year"] = m.group(0) if m else ""
    except Exception:
        pass
    return meta


def extract_text_fb2(path, head_pages, tail_pages, stats=None):
    data = _read_fb2_bytes(path)
    if stats is not None:
        stats["embedded"] = _fb2_metadata(data)
    try:
        root = ET.fromstring(data)
        # <binary> — картинки в base64, в текст их не берём; description нужен ради ISBN и автора
        parts = [" ".join(child.itertext()) for child in root if _local_name(child.tag) != "binary"]
    except ET.ParseError:
        # Многие fb2 — невалидный XML; html.parser читает их терпимо
        soup = BeautifulSoup(data, "html.parser")
        for binary in soup.find_all("binary"):
            binary.decompose()
        parts = [soup.get_text(" ")]
    return _split_head_tail_words(" ".join(parts).split(), head_pages, tail_pages)
