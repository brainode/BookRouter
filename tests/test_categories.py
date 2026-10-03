import pytest

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
        assert "Fiction priorities:" in system
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
