# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 The ScanBookShelf Authors

import os
import pytesseract
import fitz  # PyMuPDF
from ebooklib import epub
from bs4 import BeautifulSoup
import xml.etree.ElementTree as ET
import concurrent.futures
from PIL import Image
import io
import numpy as np
# import pymupdf
import subprocess
from tempfile import TemporaryDirectory
import shutil
import re
import unicodedata

from config import (
    DDJVU_PAGE_TIMEOUT_SEC,
    LANGUAGES,
    MAX_PAGES,
    MAX_TAIL_PAGES,
    OCR_ENABLED,
    OCR_PAGE_TIMEOUT_SEC,
    TESSERACT_CMD,
    WORDS_PER_PAGES,
)

# Проверяем, установлен ли путь к tesseract
if OCR_ENABLED and TESSERACT_CMD:
    pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD


class OCRTimeoutError(RuntimeError):
    """Таймаут OCR/DDJVU для маршрутизации файла в Errors."""


def _is_timeout_message(message: str) -> bool:
    lowered = str(message or "").lower()
    return "timeout" in lowered or "timed out" in lowered


def prepare_book_path(path):
    """
    Проверяет имя файла и при наличии кириллицы копирует во временную папку с транслитерацией.
    Возвращает (новый_путь, temp_dir_obj).
    temp_dir_obj нужно хранить, пока используется файл, чтобы он не удалился.
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

    filename = os.path.basename(path)
    
    # Проверка на наличие кириллицы
    if re.search(r'[А-Яа-яЁё]', filename):
        temp_dir = TemporaryDirectory()
        safe_name = transliterate(filename)
        safe_name = re.sub(r'[^A-Za-z0-9._-]', '_', safe_name)  # убираем опасные символы
        new_path = os.path.join(temp_dir.name, safe_name)
        shutil.copy2(path, new_path)
        return new_path, temp_dir
    else:
        return path, None

def needs_ocr(text):
    """Определяет, нужно ли применять OCR к документу"""
    # Если текст не удалось извлечь или его слишком мало - нужен OCR
    if not text or len(text.split()) < 50:
        return True
    return False

def perform_ocr_on_page(page):
    """Выполняет OCR на одной странице документа"""
    try:
        # Получаем изображение страницы с высоким разрешением
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
        
        # Конвертируем в формат для Pillow
        img = Image.open(io.BytesIO(pix.tobytes(output="png")))
        
        # Применяем OCR с распознаванием указанных языков
        timeout_sec = max(1, int(OCR_PAGE_TIMEOUT_SEC))
        text = pytesseract.image_to_string(img, lang=LANGUAGES, timeout=timeout_sec)
        return text
    except RuntimeError as e:
        if _is_timeout_message(e):
            raise OCRTimeoutError(f"ocr_timeout>{max(1, int(OCR_PAGE_TIMEOUT_SEC))}s") from e
        print(f"Ошибка OCR: {e}")
        return ""
    except Exception as e:
        print(f"Ошибка OCR: {e}")
        return ""

def extract_text_with_ends(path, head_pages=MAX_PAGES, tail_pages=MAX_TAIL_PAGES):
    """
    Извлекает текст из первых head_pages и последних tail_pages страниц.
    Для fb2/epub tail_pages трактуются как последние блоки текста.
    """
    text_head = extract_text(path, max_pages=head_pages, part="head")
    text_tail = extract_text(path, max_pages=tail_pages, part="tail")
    return [text_head,text_tail]

def extract_text_pdf(path, max_pages, part="head"):
    """Извлекает текст из PDF с поддержкой OCR"""
    text = ""
    try:
        doc = fitz.open(path)
        if part == "head":
            page_range = range(min(max_pages, len(doc)))
        elif part == "tail":
            page_range = range(max(0, len(doc) - max_pages), len(doc))
        else:
            raise ValueError("part должен быть 'head' или 'tail'")
        
        # Сначала пробуем стандартное извлечение текста
        for page_number in page_range:
            page = doc[page_number]
            page_text = page.get_text()
            text += page_text + "\n"
        
        # Проверяем, нужно ли OCR
        if OCR_ENABLED and needs_ocr(text):
            print(f"🔍 Применяю OCR для PDF: {os.path.basename(path)}")
            text = ""  # Сбрасываем текст, т.к. будем использовать OCR
            
            # Применяем OCR к страницам
            for page_number in page_range:
                page = doc[page_number]
                page_text = perform_ocr_on_page(page)
                text += page_text + "\n"
    except OCRTimeoutError:
        raise
    except Exception as e:
        print(f"Ошибка при извлечении текста из PDF: {e}")
    
    return text.strip()

def extract_text_djvu(path, max_pages, part="head"):
    """Извлекает текст из DJVU с OCR через ddjvu, используя perform_ocr_on_page"""
    safe_path, temp_dir = prepare_book_path(path)
    text = ""
    timeout_sec = max(1, int(DDJVU_PAGE_TIMEOUT_SEC))
    with TemporaryDirectory() as tmpdir:
        try:
            proc = subprocess.run(
                ["djvused.exe", "-e", "n", safe_path],
                capture_output=True,
                text=True,
                timeout=timeout_sec,
            )
            total_pages = int(proc.stdout.strip())
            if part == "head":
                page_range = range(min(max_pages, total_pages))
            elif part == "tail":
                page_range = range(max(0, total_pages - max_pages), total_pages)
            for page_num in page_range:
                out_file = os.path.join(tmpdir, f"page{page_num}.tiff")
                try:
                    subprocess.run(
                        [
                            "ddjvu.exe",
                            "-format=tiff",
                            f"-page={page_num}",
                            safe_path,
                            out_file,
                        ],
                        check=True,
                        timeout=timeout_sec,
                        capture_output=True,
                        text=True,
                    )
                except subprocess.TimeoutExpired as exc:
                    raise OCRTimeoutError(f"ddjvu_timeout>{timeout_sec}s page={page_num}") from exc

                # Открываем TIFF
                img = Image.open(out_file)

                # Конвертируем в PNG в память
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                buf.seek(0)

                # Создаём fitz.Document из PNG
                doc = fitz.open("png", buf.read())
                page = doc[0]  # всегда одна страница

                # Используем уже существующую функцию
                page_text = perform_ocr_on_page(page)
                text += page_text + "\n"
                print(f"📄 OCR страница {page_num}/{MAX_PAGES}")

        except subprocess.TimeoutExpired as exc:
            raise OCRTimeoutError(f"ddjvu_timeout>{timeout_sec}s") from exc
        except OCRTimeoutError:
            raise
        except FileNotFoundError:
            print("❌ ddjvu не найден! Установи DjVuLibre и добавь его в PATH.")
        except subprocess.CalledProcessError as e:
            print(f"Ошибка при обработке DJVU: {e}")
        finally:
            if temp_dir:  # удаляем временный каталог
                temp_dir.cleanup()

    return text.strip()

def extract_text_epub(path, max_pages=1000, part="head"):
    book = epub.read_epub(path)
    words_in_pages = WORDS_PER_PAGES * max_pages
    items = [item for item in book.get_items() if item.get_type() == 9]  # Только DOCUMENT
    
    # Если tail — идем с конца
    if part == "tail":
        items = reversed(items)

    words = []
    for item in items:
        soup = BeautifulSoup(item.get_content(), 'html.parser')
        words.extend(soup.get_text().split())

        if len(words) >= words_in_pages:
            break
    
    # Если брали с конца — переворачиваем обратно, чтобы текст был читаемым
    if part == "tail":
        # print(words)
        words = words[-len(words):]
        # print(words)
    else:
        words = words[:words_in_pages]

    return " ".join(words)


def extract_text_fb2(path, max_pages=1000, part="head"):
    words_in_pages = WORDS_PER_PAGES * max_pages
    try:
        tree = ET.parse(path)
        root = tree.getroot()

        # Все текстовые элементы
        texts = [elem.text.strip() for elem in root.iter() if elem.text]

        if part == "tail":
            selected = texts[-words_in_pages:]
        else:
            selected = texts[:words_in_pages]

        return " ".join(selected)
    except Exception:
        return ""
    
def extract_text(path, max_pages, part="head"):
    """Извлекает текст из книги в зависимости от формата"""
    print(f"📄 Извлечение текста из: {os.path.basename(path)}")
    
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        return extract_text_pdf(path, max_pages, part)
    elif ext in (".djvu", ".djv"):
        return extract_text_djvu(path, max_pages, part)
    elif ext == ".epub":
        return extract_text_epub(path, max_pages, part)
    elif ext == ".fb2":
        return extract_text_fb2(path, max_pages, part)
    return ""
