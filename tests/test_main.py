import sys
from datetime import timedelta
import pytest
import requests
import common
import report

pytestmark = pytest.mark.usefixtures("report_http")


def test_main_surfaces_fetch_errors(monkeypatch, capsys):
    common.CONFIG_PATH.write_text('{"feeds": ["http://x/feed"]}')
    def fail(*args, **kwargs):
        raise requests.Timeout("boom")
    monkeypatch.setattr(requests, "get", fail)
    monkeypatch.setattr(sys, "argv", ["report.py", "weekly"])
    with pytest.raises(SystemExit) as exc:
        report.main()
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "http://x/feed" in err and "boom" in err


def test_main_weekly_prints_failed_feeds_footer(monkeypatch, capsys):
    con = common.connect()
    common.set_feed_status(con, "https://broken/feed", error="HTTP 404")
    con.close()
    monkeypatch.setattr(sys, "argv", ["report.py", "weekly", "--no-fetch"])
    report.main()
    out = capsys.readouterr().out
    assert "⚠️" in out and "https://broken/feed" in out


def test_main_pulse_marks_alerted(monkeypatch, capsys):
    con = common.connect()
    now = common.now_utc()
    con.execute("INSERT INTO term_counts VALUES ('история', ?, 'old')",
                (common.day_of(now - timedelta(days=20)),))
    for feed in ("f1", "f2", "f3"):
        con.execute("INSERT INTO term_counts VALUES ('агент', ?, ?)",
                    (common.day_of(now), feed))
    con.commit()
    common.ENV_PATH.write_text("TELEGRAM_TOKEN=test\nTELEGRAM_CHAT_ID=123\n")
    class Response:
        def raise_for_status(self):
            pass
    monkeypatch.setattr(requests, "post", lambda *a, **k: Response())
    monkeypatch.setattr(sys, "argv", ["report.py", "pulse", "--send", "--no-fetch"])
    report.main()
    out = capsys.readouterr().out
    assert "агент" in out
    assert [r[0] for r in con.execute("SELECT term FROM pulse_alerts")] == ["агент"]
