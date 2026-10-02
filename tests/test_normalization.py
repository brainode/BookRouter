import pytest

from normalization import extract_first_valid_isbn, normalize_isbn, parse_filename_hints, to_ascii_slug


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("978-0-306-40615-7", "9780306406157"),
        ("0-306-40615-2", "9780306406157"),  # ISBN-10 переводится в ISBN-13
        ("978-0-306-40615-8", None),  # неверная контрольная цифра
        ("", None),
        (None, None),
    ],
)
def test_normalize_isbn(raw, expected):
    assert normalize_isbn(raw) == expected


def test_extract_first_valid_isbn_skips_invalid():
    text = "ISBN 978-0-306-40615-8 (опечатка), правильный ISBN 978-0-306-40615-7"
    assert extract_first_valid_isbn(text) == "9780306406157"


def test_to_ascii_slug():
    assert to_ascii_slug("Иван Петров") == "ivan-petrov"
    assert to_ascii_slug("", fallback="x") == "x"


@pytest.mark.parametrize(
    "filename, title, author, series",
    [
        ("Иван Петров - Калоша (худ.А.Сидоров) - 1971.epub", "Калоша", "Иван Петров", ""),
        ("Иван Петров - Тыква (худ.В.Козлов)  [Весёлые книжки] - 1982.epub", "Тыква", "Иван Петров", "Весёлые книжки"),
        ("Смит Дж. - Старый Мост (пер. Орлов М.) - 1988.epub", "Старый Мост", "Смит Дж.", ""),
        ("Сидоров А.С. - Морской Берег.fb2", "Морской Берег", "Сидоров А.С.", ""),
        ("Большие данные Краткое руководство - Джон Доу.fb2", "Большие данные Краткое руководство", "Джон Доу", ""),
        (
            "Статистика для всех, LEGO и другое [2021] Джон Доу.epub",
            "Статистика для всех, LEGO и другое",
            "Джон Доу",
            "",
        ),
        (
            "Танака Хиро - Занимательная химия (Учебная манга) - 2016.djvu",
            "Занимательная химия",
            "Танака Хиро",
            "Учебная манга",
        ),
        ("Tidy Scripts - John Q. Public.pdf", "Tidy Scripts", "John Q. Public", ""),
        ("Smith_Jones_(vol.01).djv", "Smith Jones", "Неизвестный автор", ""),
    ],
)
def test_parse_filename_hints(filename, title, author, series):
    assert parse_filename_hints(filename) == {"title": title, "author": author, "series": series}
