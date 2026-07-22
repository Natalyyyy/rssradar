import common
import report


def test_build_weekly_fallback_lists_links(monkeypatch):
    con = common.connect(":memory:")
    common.save_entries(con, [{
        "feed_url": "f1", "entry_id": "e1", "title": "Тема дня",
        "link": "https://x/e1", "published": "", "summary": ""}])
    monkeypatch.setattr(report, "claude_summary", lambda entries, path: None)
    cfg = common.DEFAULTS.copy()
    text = report.build_weekly(con, cfg)
    assert "https://x/e1" in text          # ссылка есть даже без Claude


def test_build_weekly_uses_claude_when_available(monkeypatch):
    con = common.connect(":memory:")
    common.save_entries(con, [{
        "feed_url": "f1", "entry_id": "e1", "title": "Тема",
        "link": "https://x/e1", "published": "", "summary": ""}])
    monkeypatch.setattr(report, "claude_summary",
                         lambda entries, path: "🔥 Горячее\n• тема — https://x/e1")
    cfg = common.DEFAULTS.copy()
    text = report.build_weekly(con, cfg)
    assert "Горячее" in text


def test_claude_summary_returns_none_on_bad_prompt_path():
    entries = [{"title": "t", "link": "https://x/1"}]
    result = report.claude_summary(entries, "/nonexistent/prompt/path.md")
    assert result is None


def test_format_failed_feeds_empty_when_all_ok():
    con = common.connect(":memory:")
    common.set_feed_status(con, "https://ok/feed")
    assert report.format_failed_feeds(con) == ""


def test_format_failed_feeds_lists_broken_feeds():
    con = common.connect(":memory:")
    common.set_feed_status(con, "https://broken/feed", error="HTTP 404")
    text = report.format_failed_feeds(con)
    assert "1 фидов не ответили" in text
    assert "https://broken/feed" in text


def test_format_failed_feeds_clears_after_recovery():
    """last_error чистится set_feed_status при успехе — фид не должен висеть вечно."""
    con = common.connect(":memory:")
    common.set_feed_status(con, "https://flaky/feed", error="таймаут")
    assert "flaky" in report.format_failed_feeds(con)
    common.set_feed_status(con, "https://flaky/feed", etag="e1", modified="m1")
    assert report.format_failed_feeds(con) == ""


def test_build_weekly_appends_failed_feeds_footer(monkeypatch):
    con = common.connect(":memory:")
    common.save_entries(con, [{
        "feed_url": "f1", "entry_id": "e1", "title": "Тема",
        "link": "https://x/e1", "published": "", "summary": ""}])
    common.set_feed_status(con, "https://broken/feed", error="HTTP 500")
    monkeypatch.setattr(report, "claude_summary", lambda entries, path: None)
    cfg = common.DEFAULTS.copy()
    text = report.build_weekly(con, cfg) + report.format_failed_feeds(con)
    assert "⚠️" in text and "https://broken/feed" in text
