import pytest
import scrape_sites


@pytest.mark.parametrize("first,expected", [
    ('<a href="/news/first"><h2>First</h2></a>', [("First", ""), ("Second", "2026-08-25")]),
    ('<a href="/news/first"></a>', [("Second", "2026-08-25")]),
    ("<a href='/news/first'><h2>First</h2></a>", [("First", ""), ("Second", "2026-08-25")]),
])
def test_card_fields_do_not_leak_from_next_card(first, expected):
    second = '<a href="/news/second"><h2>Second</h2><time>Aug 25, 2026</time></a>'
    result = scrape_sites.parse_index(first + second, "news")
    assert [(r["title"], r["published"][:10]) for r in result] == expected


def test_large_card_keeps_its_own_date():
    html = '<a href="/news/first"><h2>First</h2>' + ' ' * 3000 + '<time>Aug 25, 2026</time></a>'
    assert scrape_sites.parse_index(html, "news")[0]["published"].startswith("2026-08-25")


def test_empty_duplicate_and_invalid_date():
    assert scrape_sites.parse_index("", "news") == []
    card = '<a href="/news/first"><h2>First</h2><time>Feb 31, 2026</time></a>'
    for _ in range(2):
        result = scrape_sites.parse_index(card * 2, "news")
        assert len(result) == 1 and result[0]["published"] == ""
