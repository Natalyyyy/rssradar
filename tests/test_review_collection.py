import sys
import pytest
import common
import report
import requests


def test_pulse_reports_stored_feed_failure_without_signals(monkeypatch, capsys):
    con = common.connect()
    common.set_feed_status(con, "broken-feed", error="HTTP 500")
    con.close()
    monkeypatch.setattr(sys, "argv", ["report.py", "pulse", "--no-fetch"])
    report.main()
    assert "broken-feed" in capsys.readouterr().out


def test_collection_exception_is_not_a_quiet_success(monkeypatch, capsys):
    common.CONFIG_PATH.write_text('{"feeds": 42}')
    monkeypatch.setattr(sys, "argv", ["report.py", "pulse"])
    with pytest.raises(SystemExit) as exc:
        report.main()
    assert exc.value.code == 1
    output = capsys.readouterr()
    assert "не удалось собрать" in output.err
    assert "Тихо" not in output.out


def test_network_failures_have_nonzero_exit_even_without_signals(monkeypatch, capsys):
    def fail(*a, **k):
        raise requests.Timeout("timeout")
    monkeypatch.setattr(requests, "get", fail)
    monkeypatch.setattr(sys, "argv", ["report.py", "pulse"])
    with pytest.raises(SystemExit) as exc:
        report.main()
    assert exc.value.code == 1
    output = capsys.readouterr()
    assert "timeout" in output.err and "не ответили" in output.out
