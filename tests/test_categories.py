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
    monkeypatch.setattr(llm, "_chat_json", lambda *args, **kwargs: response)
    assert llm.classify_category("text", "Title", "Author") == {
        "category": "Требует внимания", "confidence": 0.0, "evidence": "",
        "review_reason": "invalid_category_response" if response.get("category") != "Требует внимания" else "insufficient_category_evidence",
    }


def test_classifier_receives_subject_rules_and_normalizes_case(monkeypatch):
    def chat(system, prompt, schema, temperature=None):
        assert "Cheatsheets and reference guides belong to their subject" in system
        assert "Fiction genres:" in system
        assert "mathematical foundations" in system
        assert "Требует внимания" in schema["properties"]["category"]["enum"]
        assert "Title: Python reference" in prompt
        return {"category": "it | языки программирования | python", "confidence": 0.8}

    monkeypatch.setattr(llm, "_chat_json", chat)
    assert llm.classify_category("text", "Python reference", "Author") == {
        "category": "IT | Языки программирования | Python", "confidence": 0.8, "review_reason": "", "evidence": ""
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
    monkeypatch.setattr(llm, "_chat_json", lambda *args, **kwargs: {"category": "IT | Data Science", "confidence": 0.2})
    result = llm.classify_category("text", "Title", "Author")
    assert result["category"] == "Требует внимания"
    assert "suggested=IT | Data Science" in result["review_reason"]


def test_prompt_files_loaded():
    assert "IT | AI и ML" in llm.ALLOWED_CATEGORIES
    assert llm.DEFAULT_CATEGORY in llm.ALLOWED_CATEGORIES
    assert "{excerpt}" in config.FACTS_USER_PROMPT


def test_classify_prompt_contains_rules(monkeypatch):
    seen = {}

    def fake(system_prompt, user_prompt, schema, temperature=None):
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


def test_classify_returns_evidence_and_uses_classify_temperature(monkeypatch):
    seen = {}

    def chat(system, prompt, schema, temperature=None):
        seen["temperature"] = temperature
        return {"evidence": "про Python", "category": "IT | Языки программирования | Python", "confidence": 0.9}

    monkeypatch.setattr(llm, "_chat_json", chat)
    result = llm.classify_category("text", "T", "A")
    assert result["evidence"] == "про Python"
    assert seen["temperature"] == config.LLM_CLASSIFY_TEMPERATURE


def test_facts_edition_fields_validated(monkeypatch):
    data = {"title": "T", "author": "A", "pub_year": "1999г", "edition": "2", "publisher": "  Питер "}
    monkeypatch.setattr(llm, "_chat_json", lambda *a, **k: dict(data))
    facts = llm.extract_book_facts("text", "f.pdf")
    assert (facts["pub_year"], facts["edition"], facts["publisher"]) == ("", "2", "Питер")
    data["edition"] = "1"
    assert llm.extract_book_facts("text", "f.pdf")["edition"] == ""


def test_fb2_decisive_genres_are_allowed_categories():
    import genres
    assert set(genres.DECISIVE_FB2_GENRES.values()) <= set(llm.ALLOWED_CATEGORIES)


def test_fb2_decision():
    from genres import fb2_decision
    assert fb2_decision(["sf_horror", "sf"]) == "Художественные | Ужасы"
    assert fb2_decision(["sf"]) == ""
    assert fb2_decision(["sf_fantasy", "sf_horror"]) == ""
    assert fb2_decision(["child_tale", "sf_fantasy"]) == "Художественные | Детская"


def test_decide_category_uses_fb2_without_llm(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("LLM called")
    monkeypatch.setattr(llm, "_chat_json", boom)
    assert llm.decide_category("t", "T", "A", ["child_tale"])["source"] == "fb2"


def test_decide_category_passes_hints_to_llm(monkeypatch):
    seen = {}

    def chat(system, prompt, schema, temperature=None):
        seen["prompt"] = prompt
        return {"category": "Требует внимания", "confidence": 0.1}
    monkeypatch.setattr(llm, "_chat_json", chat)
    result = llm.decide_category("t", "T", "A", ["sf"], ["Fiction"])
    assert "FB2 genre tags: sf" in seen["prompt"]
    assert "Publisher subjects: Fiction" in seen["prompt"]
    assert result["source"] == "llm"
