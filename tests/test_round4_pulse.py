"""Regression 1: draining an old pulse must not skip the current window."""
from datetime import datetime, timedelta, timezone

import pytest
import requests

import common
import report
from tests.test_round2 import Clock, Response


@pytest.mark.parametrize("failure", [None, "old", "current"])
@pytest.mark.parametrize("has_current", [False, True])
def test_pending_pulse_checks_current_window(monkeypatch, failure, has_current):
    original_failure = failure
    Clock.instant = datetime(2026, 10, 21, tzinfo=timezone.utc)
    monkeypatch.setattr(common, "datetime", Clock)
    con = common.connect()
    cfg = dict(common.DEFAULTS, pulse_window_days=1)
    env = {"TELEGRAM_TOKEN": "test", "TELEGRAM_CHAT_ID": "123"}
    con.execute("INSERT INTO term_counts VALUES ('history', '2026-09-01', 'f1')")
    for feed in ("f1", "f2"):
        con.execute("INSERT INTO term_counts VALUES ('old', '2026-10-20', ?)", (feed,))
        if has_current:
            con.execute("INSERT INTO term_counts VALUES ('current', '2026-10-21', ?)", (feed,))
    con.execute("INSERT INTO pulse_queue VALUES (1, 'old payload', '[\"old\"]', '123')")
    con.commit()
    attempts = []
    def post(*args, **kwargs):
        text = kwargs["json"]["text"]
        attempts.append(text)
        if failure == "old" or (failure == "current" and text != "old payload"):
            raise requests.HTTPError("synthetic failure")
        return Response()
    monkeypatch.setattr(requests, "post", post)
    if failure == "old" or (failure == "current" and has_current):
        with pytest.raises(requests.HTTPError):
            report.deliver_pulse(con, cfg, env)
        if failure == "old":
            assert attempts == ["old payload"]
            assert con.execute("SELECT count(*) FROM pulse_alerts").fetchone()[0] == 0
        else:
            assert common.was_alerted_recently(con, "old", Clock.instant, 5)
            assert not common.was_alerted_recently(con, "current", Clock.instant, 5)
            # Retry after the signal has left the window: queued payload survives.
            Clock.instant += timedelta(days=1)
        failure = None
        con.close()
        con = common.connect()
    report.deliver_pulse(con, cfg, env)
    assert attempts[0] == "old payload"
    assert attempts.count("old payload") == (2 if original_failure == "old" else 1)
    current_attempts = [text for text in attempts if text != "old payload"]
    assert len(current_attempts) == (int(has_current) * (2 if original_failure == "current" else 1))
    if has_current:
        assert "current" in current_attempts[-1]
        assert common.was_alerted_recently(con, "current", Clock.instant, 5)
    assert con.execute("SELECT count(*) FROM pulse_queue").fetchone()[0] == 0
    assert con.execute("SELECT count(*) FROM pulse_delivery").fetchone()[0] == 0
    sent_count = len(attempts)
    report.deliver_pulse(con, cfg, env)
    assert len(attempts) == sent_count
    con.close()
