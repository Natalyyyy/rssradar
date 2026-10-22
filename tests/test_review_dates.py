from datetime import timedelta
import common
import report


def test_future_terms_cannot_create_spike_or_history():
    con = common.connect(":memory:")
    now = common.parse_ts("2026-10-21T10:00:00Z")
    for day in ("2026-10-01", "2099-01-01"):
        for feed in ("f1", "f2"):
            con.execute("INSERT INTO term_counts VALUES (?,?,?)", ("future", day, feed))
    con.commit()
    assert report.pulse_signals(con, common.DEFAULTS, now) == []
    assert common.data_span_days(con, now) == 0


def test_future_article_is_saved_but_not_counted_until_publication():
    con = common.connect(":memory:")
    record = {"feed_url":"f1", "entry_id":"future", "title":"future",
              "published":"2099-01-01T00:00:00+00:00"}
    common.save_entries(con, [record])
    for _ in range(2):
        common.record_terms(con, [record])
    assert con.execute("SELECT count(*) FROM entries").fetchone()[0] == 1
    assert con.execute("SELECT count(*) FROM term_counts").fetchone()[0] == 0


def test_links_exclude_future_and_keep_today():
    con = common.connect(":memory:")
    today = common.day_of(common.now_utc())
    common.save_entries(con, [
        {"feed_url":"f", "entry_id":"1", "title":"agent", "link":"today", "published":today},
        {"feed_url":"f", "entry_id":"2", "title":"agent", "link":"future", "published":"2099-01-01"},
    ])
    assert report.term_links(con, "agent", today) == [("agent", "today")]


def test_current_day_counts_and_missing_date_fallback():
    con = common.connect(":memory:")
    records = [{"feed_url":"f", "entry_id":str(i), "title":"agent", "published":date}
               for i, date in enumerate([common.now_utc().isoformat(), "", "broken"])]
    common.save_entries(con, records)
    common.record_terms(con, records)
    assert [r[0] for r in con.execute("SELECT day FROM term_counts")] == [common.day_of(common.now_utc())]


def test_future_terms_become_due_even_when_feed_returns_304(monkeypatch):
    from datetime import datetime, timezone
    import fetch
    from tests.test_fetch import FakeResponse
    class Clock(datetime):
        instant = datetime(2026, 10, 21, tzinfo=timezone.utc)
        @classmethod
        def now(cls, tz=None):
            return cls.instant
    monkeypatch.setattr(common, "datetime", Clock)
    con = common.connect(":memory:")
    xml = b'''<rss version="2.0"><channel><title>Test</title><item>
    <guid>future</guid><title>agent</title><pubDate>Thu, 22 Oct 2026 00:00:00 GMT</pubDate>
    </item></channel></rss>'''
    replies = iter([FakeResponse(200, xml, {"ETag":"v1"}), FakeResponse(304), FakeResponse(304)])
    monkeypatch.setattr(fetch.requests, "get", lambda *a, **k: next(replies))
    assert fetch.run_fetch({"feeds":["feed"]}, con) == (1, {})
    assert con.execute("SELECT count(*) FROM term_counts").fetchone()[0] == 0
    Clock.instant = datetime(2026, 10, 22, tzinfo=timezone.utc)
    for _ in range(2):
        assert fetch.run_fetch({"feeds":["feed"]}, con) == (0, {})
        assert [tuple(r) for r in con.execute("SELECT * FROM term_counts")] == [
            ("agent", "2026-10-22", "feed")]
