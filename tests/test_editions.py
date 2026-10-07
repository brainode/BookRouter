# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 The ScanBookShelf Authors

from editions import edition_relation, parse_edition, same_work, volume_marker, work_key


def test_parse_edition():
    assert parse_edition("Грокаем алгоритмы. 2-е изд.") == "2"
    assert parse_edition("Издание второе, исправленное") == "2"
    assert parse_edition("Clean Code, Second Edition") == "2"
    assert parse_edition("3rd ed.") == "3"
    assert parse_edition("1-е издание") == ""


def test_volume_marker():
    assert volume_marker("Курс высшей математики. Том 4") == "4"
    assert volume_marker("Vol. III") == "3"
    assert volume_marker("Книга джунглей") == ""


def test_same_work():
    k = lambda t: work_key(1, "Автор", t)
    assert same_work(k("Грокаем алгоритмы"), k("Грокаем алгоритмы. Иллюстрированное пособие"))
    assert not same_work(k("Курс высшей математики. Том 1"), k("Курс высшей математики. Том 4"))
    assert not same_work(k("Основы"), k("Основы химии"))


def test_edition_relation_rules():
    assert edition_relation({"isbn_norm": "1"}, {"isbn_norm": "1", "edition": "2"}) == "same"
    assert edition_relation({"edition": "1"}, {"edition": "2"}) == "different"
    assert edition_relation({"pub_year": "2010"}, {"pub_year": "2015"}) == "different"
    assert edition_relation({"edition": "2"}, {"edition": "2"}) == "same"
    assert edition_relation({"pub_year": "2010", "publisher": "Питер"}, {"pub_year": "2011", "publisher": ""}) == "same"
    assert edition_relation({"pub_year": "2010", "publisher": "Питер"}, {"pub_year": "2010", "publisher": "Эксмо"}) == "unknown"
    assert edition_relation({"isbn_norm": "1"}, {"isbn_norm": "2"}) == "unknown"
