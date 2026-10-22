import sqlite3
import pytest
import common
import fetch
from tests.test_fetch import FakeResponse, FIXTURE


@pytest.mark.parametrize("table", ["entries", "term_counts"])
def test_fetch_rolls_back_cache_and_records_on_write_error(monkeypatch, table):
    con = common.connect(":memory:")
    common.set_feed_status(con, "feed", etag="old", modified="old-date")
    con.execute("CREATE TRIGGER fail_write BEFORE INSERT ON " + table +
                " BEGIN SELECT RAISE(ABORT, 'disk failure'); END")
    monkeypatch.setattr(fetch.requests, "get", lambda *a, **k:
                        FakeResponse(200, FIXTURE.read_bytes(), {"ETag": "new"}))
    try:
        fetch.run_fetch({"feeds": ["feed"]}, con)
    except sqlite3.DatabaseError:
        pass
    assert common.get_feed_cache(con, "feed") == ("old", "old-date")
    assert con.execute("SELECT count(*) FROM entries").fetchone()[0] == 0
    assert con.execute("SELECT count(*) FROM term_counts").fetchone()[0] == 0
    con.execute("DROP TRIGGER fail_write")
    assert fetch.run_fetch({"feeds": ["feed"]}, con) == (2, {})
    assert fetch.run_fetch({"feeds": ["feed"]}, con) == (0, {})
    assert common.get_feed_cache(con, "feed")[0] == "new"


@pytest.mark.parametrize("status", [304, 500, 429])
def test_fetch_empty_and_http_errors_preserve_cache(monkeypatch, status):
    con = common.connect(":memory:")
    common.set_feed_status(con, "feed", etag="old")
    monkeypatch.setattr(fetch.requests, "get", lambda *a, **k: FakeResponse(status))
    count, errors = fetch.run_fetch({"feeds": ["feed"]}, con)
    assert count == 0
    assert bool(errors) == (status != 304)
    assert common.get_feed_cache(con, "feed")[0] == "old"


def test_fetch_timeout_keeps_cache(monkeypatch):
    con = common.connect(":memory:")
    common.set_feed_status(con, "feed", etag="old")
    def timeout(*a, **k):
        raise fetch.requests.Timeout("timeout")
    monkeypatch.setattr(fetch.requests, "get", timeout)
    assert fetch.run_fetch({"feeds": ["feed"]}, con) == (0, {"feed": "timeout"})
    assert common.get_feed_cache(con, "feed")[0] == "old"


def test_scrape_rolls_back_entries_when_terms_cannot_be_saved(monkeypatch):
    import scrape_sites
    from tests.test_scrape_sites import NEWS_HTML, _Resp
    con = common.connect(":memory:")
    con.execute("CREATE TRIGGER fail_write BEFORE INSERT ON term_counts "
                "BEGIN SELECT RAISE(ABORT, 'disk failure'); END")
    monkeypatch.setattr(scrape_sites.requests, "get", lambda *a, **k: _Resp(NEWS_HTML))
    monkeypatch.setattr(scrape_sites, "SITES", [("scrape:anthropic/news", "https://x", "news")])
    try:
        scrape_sites.run_scrape(con)
    except sqlite3.DatabaseError:
        pass
    assert con.execute("SELECT count(*) FROM entries").fetchone()[0] == 0
    assert con.execute("SELECT count(*) FROM term_counts").fetchone()[0] == 0
    con.execute("DROP TRIGGER fail_write")
    assert scrape_sites.run_scrape(con) == (2, {})
    assert scrape_sites.run_scrape(con) == (0, {})
