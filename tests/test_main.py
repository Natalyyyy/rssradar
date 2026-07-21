import sys
import common
import fetch
import report


def test_main_surfaces_fetch_errors(monkeypatch, capsys):
    monkeypatch.setattr(fetch, "run_fetch",
                        lambda cfg, con: (0, {"http://x/feed": "boom"}))
    real_connect = common.connect
    monkeypatch.setattr(common, "connect", lambda: real_connect(":memory:"))
    monkeypatch.setattr(sys, "argv", ["report.py", "weekly"])
    report.main()
    err = capsys.readouterr().err
    assert "http://x/feed" in err and "boom" in err


def test_main_pulse_marks_alerted(monkeypatch, capsys):
    real_connect = common.connect
    monkeypatch.setattr(common, "connect", lambda: real_connect(":memory:"))
    monkeypatch.setattr(fetch, "run_fetch", lambda cfg, con: (0, {}))
    monkeypatch.setattr(report, "pulse_signals",
                        lambda con, cfg, now=None: [
                            {"term": "агент", "recent_feeds": 3, "recent_daily": 2.5,
                             "baseline_daily": 1.0, "ratio": 2.5,
                             "links": [("Заголовок", "https://x/1")]}])
    marked = {}
    monkeypatch.setattr(common, "mark_alerted",
                        lambda con, terms, at=None: marked.setdefault("terms", terms))
    monkeypatch.setattr(sys, "argv", ["report.py", "pulse"])
    report.main()
    out = capsys.readouterr().out
    assert "агент" in out
    assert marked.get("terms") == ["агент"]
