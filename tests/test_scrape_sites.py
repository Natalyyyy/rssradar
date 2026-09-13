"""Парсинг карточек с индекс-страниц Anthropic (фида у них нет)."""
import scrape_sites

# две разные раскладки карточек, которые реально отдаёт anthropic.com
NEWS_HTML = (
    '<a href="/news/claude-opus-5" class="content">'
    '<h2 class="headline-4">Introducing Claude Opus 5</h2>'
    '<div class="meta"><time class="date">Jul 24, 2026</time></div></a>'
    '<a href="/news/wellbeing-research-grants" class="listItem">'
    '<div class="meta"><time class="PublicationList__date body-3">Aug 25, 2026</time></div>'
    '<span class="PublicationList__title body-3">Funding better evaluations</span></a>'
)

ENG_HTML = (
    '<article><a href="/engineering/demystifying-evals-for-ai-agents">'
    '<div class="content"><h3 class="headline-4">Demystifying evals for AI agents</h3>'
    '<div class="body-2 ArticleList__date">Jan 09, 2026</div></div></a></article>'
)


def test_parses_both_news_layouts():
    items = scrape_sites.parse_index(NEWS_HTML, "news")
    assert [i["title"] for i in items] == [
        "Introducing Claude Opus 5", "Funding better evaluations"]
    assert items[0]["link"] == "https://www.anthropic.com/news/claude-opus-5"
    assert items[0]["published"].startswith("2026-07-24")
    assert items[1]["published"].startswith("2026-08-25")
    assert items[0]["feed_url"] == "scrape:anthropic/news"


def test_parses_engineering_cards():
    items = scrape_sites.parse_index(ENG_HTML, "engineering")
    assert len(items) == 1
    assert items[0]["title"] == "Demystifying evals for AI agents"
    assert items[0]["published"].startswith("2026-01-09")


def test_skips_duplicate_links():
    items = scrape_sites.parse_index(NEWS_HTML + NEWS_HTML, "news")
    assert len(items) == 2


def test_entry_without_title_is_skipped():
    assert scrape_sites.parse_index('<a href="/news/orphan"></a>', "news") == []


# --- run_scrape: что происходит, когда сайт подводит ---------------------

import common
import pytest


class _Resp:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        return None


def _only_news(monkeypatch):
    """Оставить один сайт: три реальных запроса в тесте не нужны."""
    monkeypatch.setattr(scrape_sites, "SITES", [
        ("scrape:anthropic/news", "https://www.anthropic.com/news", "news"),
    ])


def test_run_scrape_saves_cards(monkeypatch):
    _only_news(monkeypatch)
    monkeypatch.setattr(scrape_sites.requests, "get",
                        lambda url, **kw: _Resp(NEWS_HTML))
    con = common.connect(":memory:")

    n_new, errors = scrape_sites.run_scrape(con=con)

    assert (n_new, errors) == (2, {})
    assert con.execute("SELECT COUNT(*) FROM entries").fetchone()[0] == 2


def test_network_failure_lands_in_errors_not_in_a_crash(monkeypatch):
    """Сайт лежит — скрейп не должен ронять весь сбор: фиды идут дальше."""
    _only_news(monkeypatch)

    def explode(url, **kw):
        raise scrape_sites.requests.exceptions.Timeout("истёк таймаут")

    monkeypatch.setattr(scrape_sites.requests, "get", explode)
    con = common.connect(":memory:")

    n_new, errors = scrape_sites.run_scrape(con=con)

    assert n_new == 0
    assert "истёк таймаут" in errors["scrape:anthropic/news"]
    assert con.execute(
        "SELECT last_error FROM feed_status WHERE feed_url='scrape:anthropic/news'",
    ).fetchone()[0]


def test_broken_markup_is_an_error_not_a_silent_zero(monkeypatch):
    """Разметка страницы поехала — это видимая ошибка, а не «новостей нет».

    Молчаливый ноль здесь опаснее всего: отчёт выглядит здоровым, а главный
    первоисточник ниши тихо выпал из сбора.
    """
    _only_news(monkeypatch)
    monkeypatch.setattr(scrape_sites.requests, "get",
                        lambda url, **kw: _Resp("<div>ничего похожего на карточки</div>"))
    con = common.connect(":memory:")

    n_new, errors = scrape_sites.run_scrape(con=con)

    assert n_new == 0
    assert errors["scrape:anthropic/news"] == "карточки не распарсились"


def test_limit_caps_cards_per_page(monkeypatch):
    """Холодный старт: --limit не даёт залить в базу весь архив разом."""
    _only_news(monkeypatch)
    monkeypatch.setattr(scrape_sites.requests, "get",
                        lambda url, **kw: _Resp(NEWS_HTML))
    con = common.connect(":memory:")

    n_new, _ = scrape_sites.run_scrape(con=con, limit=1)

    assert n_new == 1
