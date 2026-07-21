import requests

import discover

HTML = '''
<html><head>
<link rel="alternate" type="application/rss+xml" href="/feed.xml">
<link rel="alternate" type="application/atom+xml" href="https://cdn.example.com/atom">
</head><body></body></html>
'''

def test_feed_links_absolute():
    links = discover.feed_links_in_html("https://example.com/blog", HTML)
    assert "https://example.com/feed.xml" in links
    assert "https://cdn.example.com/atom" in links

def test_common_paths_appended():
    links = discover.feed_links_in_html("https://example.com", "<html></html>")
    assert "https://example.com/feed" in links
    assert "https://example.com/rss" in links

def test_is_valid_feed_false_on_network_error(monkeypatch):
    def raise_error(*args, **kwargs):
        raise requests.RequestException("boom")
    monkeypatch.setattr(discover.requests, "get", raise_error)
    assert discover.is_valid_feed("https://x/feed") is False

def test_discover_reports_russian_error_on_unreachable(monkeypatch):
    def raise_error(*args, **kwargs):
        raise requests.RequestException("boom")
    monkeypatch.setattr(discover.requests, "get", raise_error)
    feeds, errors = discover.discover("https://x")
    assert feeds == []
    assert errors["https://x"] == "сайт недоступен"
