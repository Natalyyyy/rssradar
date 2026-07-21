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
