from pathlib import Path
import feedparser
import common
import fetch

FIXTURE = Path(__file__).parent / "fixtures" / "sample_feed.xml"


class FakeResponse:
    def __init__(self, status_code=200, content=b"", headers=None):
        self.status_code = status_code
        self.content = content
        self.headers = headers or {}

def test_entries_from_parsed():
    parsed = feedparser.parse(FIXTURE.read_text(encoding="utf-8"))
    recs = fetch.entries_from_parsed("f1", parsed)
    assert len(recs) == 2
    assert recs[0]["entry_id"] == "a1"
    assert recs[0]["link"] == "https://example.com/a1"
    assert recs[0]["published"].startswith("2026-07-20")

def test_run_fetch_populates_db(monkeypatch):
    con = common.connect(":memory:")
    parsed = feedparser.parse(FIXTURE.read_text(encoding="utf-8"))
    monkeypatch.setattr(fetch, "_parse", lambda url, etag, modified: parsed)
    cfg = {"feeds": ["https://example.com/feed"]}
    n_new, errors = fetch.run_fetch(cfg=cfg, con=con)
    assert n_new == 2
    assert errors == {}
    assert con.execute("SELECT COUNT(*) FROM entries").fetchone()[0] == 2
    assert con.execute(
        "SELECT COUNT(*) FROM term_counts WHERE term='агент'").fetchone()[0] >= 1


def test_parse_downloads_via_requests_with_timeout(monkeypatch):
    """Регрессия: раньше feedparser.parse(url) качал фид сам, без таймаута —
    один зависший фид вешал весь запуск под launchd."""
    captured = {}

    def fake_get(url, timeout=None, headers=None):
        captured["url"], captured["timeout"], captured["headers"] = url, timeout, headers
        return FakeResponse(200, FIXTURE.read_bytes())

    monkeypatch.setattr(fetch.requests, "get", fake_get)
    parsed = fetch._parse("https://example.com/feed", None, None)
    assert captured["timeout"] == 30
    assert captured["url"] == "https://example.com/feed"
    assert len(parsed.entries) == 2


def test_parse_sends_conditional_headers_when_cached(monkeypatch):
    captured = {}

    def fake_get(url, timeout=None, headers=None):
        captured["headers"] = headers
        return FakeResponse(304)

    monkeypatch.setattr(fetch.requests, "get", fake_get)
    fetch._parse("https://example.com/feed", "etag123", "Mon, 01 Jan 2026 00:00:00 GMT")
    assert captured["headers"]["If-None-Match"] == "etag123"
    assert captured["headers"]["If-Modified-Since"] == "Mon, 01 Jan 2026 00:00:00 GMT"


def test_fetch_feed_treats_http_404_as_error(monkeypatch):
    """HTTP-статус >=400 → фид считается упавшим, даже если тело парсится
    как валидный XML (иначе 404-страница маскируется под last_ok)."""
    con = common.connect(":memory:")

    def fake_parsed(url, etag, modified):
        parsed = feedparser.parse(FIXTURE.read_text(encoding="utf-8"))  # валидный XML в теле
        parsed["status"] = 404
        return parsed

    monkeypatch.setattr(fetch, "_parse", fake_parsed)
    records, err = fetch.fetch_feed(con, "https://example.com/feed")
    assert records == []
    assert err == "HTTP 404"


def test_run_fetch_marks_feed_status_error_on_404(monkeypatch):
    con = common.connect(":memory:")

    def fake_get(url, timeout=None, headers=None):
        return FakeResponse(404, b"<html>not found</html>")

    monkeypatch.setattr(fetch.requests, "get", fake_get)
    cfg = {"feeds": ["https://example.com/feed"]}
    n_new, errors = fetch.run_fetch(cfg=cfg, con=con)
    assert n_new == 0
    assert errors == {"https://example.com/feed": "HTTP 404"}
    row = con.execute(
        "SELECT last_error FROM feed_status WHERE feed_url=?",
        ("https://example.com/feed",)).fetchone()
    assert row["last_error"] == "HTTP 404"


def test_fetch_feed_304_uses_cached_status(monkeypatch):
    con = common.connect(":memory:")

    def fake_get(url, timeout=None, headers=None):
        return FakeResponse(304)

    monkeypatch.setattr(fetch.requests, "get", fake_get)
    records, err = fetch.fetch_feed(con, "https://example.com/feed")
    assert records == [] and err is None
