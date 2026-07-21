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
