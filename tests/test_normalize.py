import common


def test_ru_lemmatization_collapses_forms():
    a = common.normalize_terms("Новые агенты выходят")
    b = common.normalize_terms("Про агентов и агента")
    assert "агент" in a
    assert "агент" in b            # разные формы → одна лемма


def test_stopwords_and_short_dropped():
    terms = common.normalize_terms("и в на AI")
    assert "и" not in terms and "в" not in terms and "на" not in terms
    assert "ai" not in terms       # короче 3 символов


def test_latin_lowercased_kept():
    terms = common.normalize_terms("Claude Agents Framework")
    assert "claude" in terms and "framework" in terms
