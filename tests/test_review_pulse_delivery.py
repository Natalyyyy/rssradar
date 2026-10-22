import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import pytest
import requests
import common
import report


def seed_pulse():
    con = common.connect()
    now = common.now_utc()
    con.execute("INSERT INTO term_counts VALUES ('history', ?, 'old')",
                (common.day_of(now - timedelta(days=20)),))
    for feed in ("f1", "f2"):
        con.execute("INSERT INTO term_counts VALUES ('agent', ?, ?)",
                    (common.day_of(now), feed))
    con.commit()
    con.close()
    common.ENV_PATH.write_text("TELEGRAM_TOKEN=test\nTELEGRAM_CHAT_ID=123\n")


class Success:
    def raise_for_status(self):
        pass


def test_preview_twice_never_suppresses_delivery(monkeypatch):
    seed_pulse()
    monkeypatch.setattr(sys, "argv", ["report.py", "pulse", "--no-fetch"])
    report.main()
    report.main()
    con = common.connect()
    assert con.execute("SELECT count(*) FROM pulse_alerts").fetchone()[0] == 0
    assert report.pulse_signals(con, common.DEFAULTS)


def test_delivery_error_does_not_start_cooldown(monkeypatch):
    seed_pulse()
    def fail(*a, **k):
        raise requests.HTTPError("HTTP 500")
    monkeypatch.setattr(requests, "post", fail)
    monkeypatch.setattr(sys, "argv", ["report.py", "pulse", "--send", "--no-fetch"])
    with pytest.raises(requests.HTTPError):
        report.main()
    con = common.connect()
    assert con.execute("SELECT count(*) FROM pulse_alerts").fetchone()[0] == 0
    assert con.execute("SELECT count(*) FROM pulse_queue").fetchone()[0] == 1


def test_success_marks_only_after_send_and_second_run_is_silent(monkeypatch):
    seed_pulse()
    calls = []
    def post(*a, **k):
        con = common.connect()
        assert con.execute("SELECT count(*) FROM pulse_alerts").fetchone()[0] == 0
        con.close()
        calls.append(k["json"]["text"])
        return Success()
    monkeypatch.setattr(requests, "post", post)
    monkeypatch.setattr(sys, "argv", ["report.py", "pulse", "--send", "--no-fetch"])
    report.main()
    report.main()
    assert len(calls) == 1
    con = common.connect()
    assert common.was_alerted_recently(con, "agent", common.now_utc(), 5)
    assert con.execute("SELECT count(*) FROM pulse_queue").fetchone()[0] == 0


def test_parallel_runs_claim_only_once(monkeypatch):
    seed_pulse()
    entered, release = threading.Event(), threading.Event()
    calls = []
    def post(*a, **k):
        calls.append(1)
        entered.set()
        assert release.wait(5)
        return Success()
    monkeypatch.setattr(requests, "post", post)
    monkeypatch.setattr(sys, "argv", ["report.py", "pulse", "--send", "--no-fetch"])
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(report.main)
        assert entered.wait(5)
        try:
            second = pool.submit(report.main)
            second.result(timeout=3)
        finally:
            release.set()
        first.result(timeout=3)
    assert calls == [1]


def test_pending_payload_survives_restart_and_mismatched_recipient(monkeypatch):
    seed_pulse()
    payloads = []
    def fail(*a, **k):
        payloads.append(k["json"]["text"])
        raise requests.ConnectionError("offline")
    monkeypatch.setattr(requests, "post", fail)
    monkeypatch.setattr(sys, "argv", ["report.py", "pulse", "--send", "--no-fetch"])
    with pytest.raises(requests.ConnectionError):
        report.main()
    con = common.connect()
    con.execute("DELETE FROM term_counts")
    con.commit()
    con.close()
    common.ENV_PATH.write_text("TELEGRAM_TOKEN=test\nTELEGRAM_CHAT_ID=456\n")
    with pytest.raises(ValueError, match="Получатель"):
        report.main()
    assert len(payloads) == 1
    common.ENV_PATH.write_text("TELEGRAM_TOKEN=test\nTELEGRAM_CHAT_ID=123\n")
    def success(*a, **k):
        payloads.append(k["json"]["text"])
        return Success()
    monkeypatch.setattr(requests, "post", success)
    report.main()
    assert payloads == [payloads[0], payloads[0]]
    assert common.connect().execute("SELECT count(*) FROM pulse_queue").fetchone()[0] == 0


def test_empty_pulse_does_not_send_or_enqueue(monkeypatch):
    common.ENV_PATH.write_text("TELEGRAM_TOKEN=test\nTELEGRAM_CHAT_ID=123\n")
    monkeypatch.setattr(sys, "argv", ["report.py", "pulse", "--send", "--no-fetch"])
    report.main()
    con = common.connect()
    assert con.execute("SELECT count(*) FROM pulse_queue").fetchone()[0] == 0
    assert con.execute("SELECT count(*) FROM pulse_alerts").fetchone()[0] == 0
