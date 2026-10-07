import pytest

import config
import llm


@pytest.mark.parametrize(
    "response",
    [
        {},
        {"category": "IT | Made up", "confidence": 0.9},
        {"category": "Требует внимания", "confidence": 0.9},
    ],
)
def test_uncertain_category_has_neutral_fallback(monkeypatch, response):
    monkeypatch.setattr(llm, "_chat_json", lambda *args: response)
    assert llm.classify_category("text", "Title", "Author") == {
        "category": "Требует внимания", "confidence": 0.0,
        "review_reason": "invalid_category_response" if response.get("category") != "Требует внимания" else "insufficient_category_evidence",
    }


def test_classifier_receives_subject_rules_and_normalizes_case(monkeypatch):
    def chat(system, prompt, schema):
        assert "Cheatsheets and reference guides belong to their subject" in system
        assert "Fiction genres:" in system
        assert "mathematical foundations" in system
        assert "Требует внимания" in schema["properties"]["category"]["enum"]
        assert "Title: Python reference" in prompt
        return {"category": "it | языки программирования | python", "confidence": 0.8}

    monkeypatch.setattr(llm, "_chat_json", chat)
    assert llm.classify_category("text", "Python reference", "Author") == {
        "category": "IT | Языки программирования | Python", "confidence": 0.8, "review_reason": ""
    }


@pytest.mark.parametrize(
    "category",
    ["Требует внимания", "Наука | Математика | Статистика", "IT | Разработка ПО | Qt"],
)
def test_nonfiction_path_keeps_subject_hierarchy(category):
    assert llm.build_category_path(category, "Иван Петров", "Series") == category


def test_renamed_fiction_genre_keeps_author_and_series():
    assert llm.build_category_path(
        "Художественные | Научная фантастика", "Иван Петров", ""
    ) == "Художественные | Научная фантастика | ivan-petrov | bez-serii"


def test_low_confidence_category_goes_to_review(monkeypatch):
    monkeypatch.setattr(llm, "_chat_json", lambda *args: {"category": "IT | Data Science", "confidence": 0.2})
    result = llm.classify_category("text", "Title", "Author")
    assert result["category"] == "Требует внимания"
    assert "suggested=IT | Data Science" in result["review_reason"]


def test_prompt_files_loaded():
    assert "IT | AI и ML" in llm.ALLOWED_CATEGORIES
    assert llm.DEFAULT_CATEGORY in llm.ALLOWED_CATEGORIES
    assert "{excerpt}" in config.FACTS_USER_PROMPT


def test_classify_prompt_contains_rules(monkeypatch):
    seen = {}

    def fake(system_prompt, user_prompt, schema):
        seen["system"] = system_prompt
        seen["user"] = user_prompt
        return {"category": "IT | AI и ML", "confidence": 0.9}

    monkeypatch.setattr(llm, "_chat_json", fake)
    llm.classify_category("txt", "T", "A")
    assert llm.CATEGORY_RULES.splitlines()[0] in seen["system"]
    assert "- IT | AI и ML" in seen["system"]
    assert "Title: T" in seen["user"]


def test_new_categories_allowed():
    for c in [
        "Публицистика",
        "Биографии и мемуары",
        "Наука | Математика | Алгебра",
        "Наука | Математика | Дискретная математика",
        "Наука | Математика | Общая и популярная",
        "Наука | Химия",
        "Наука | Биология и медицина",
        "Наука | Общественные науки",
        "Наука | Техника и инженерия",
    ]:
        assert c in llm.ALLOWED_CATEGORIES
    assert "Science fiction is not fantasy" in llm.CATEGORY_RULES
