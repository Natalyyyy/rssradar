from datetime import datetime, timedelta, timezone
import json

import pytest
import requests

import common
import fetch
import report
import telegram
from tests.test_fetch import FakeResponse


class Clock(datetime):
    instant = datetime(2026, 10, 21, tzinfo=timezone.utc)

    @classmethod
    def now(cls, tz=None):
        return cls.instant


@pytest.mark.parametrize('status', [200, 304])
def test_due_terms_survive_feed_rotation_and_limit(monkeypatch, status):
    Clock.instant = datetime(2026, 10, 21, tzinfo=timezone.utc)
    monkeypatch.setattr(common, 'datetime', Clock)
    con = common.connect()
    xml = b'''<rss version="2.0"><channel><title>Test</title><item>
    <guid>future</guid><title>agent</title><pubDate>Thu, 22 Oct 2026 00:00:00 GMT</pubDate>
    </item></channel></rss>'''
    later = b'''<rss version="2.0"><channel><title>Test</title><item>
    <guid>other</guid><title>robot</title></item></channel></rss>'''
    replies = iter([FakeResponse(200, xml), FakeResponse(status, later), FakeResponse(status, later)])
    monkeypatch.setattr(requests, 'get', lambda *a, **k: next(replies))
    assert fetch.run_fetch({'feeds': ['feed']}, con) == (1, {})
    assert con.execute('SELECT count(*) FROM term_counts').fetchone()[0] == 0
    Clock.instant += timedelta(days=1)
    for _ in range(2):
        assert fetch.run_fetch({'feeds': ['feed']}, con, limit=1)[1] == {}
        assert [tuple(r) for r in con.execute("SELECT * FROM term_counts WHERE term='agent'")] == [
            ('agent', '2026-10-22', 'feed')]


class Response:
    def __init__(self, status=200, retry_after=None):
        self.status_code = status
        self.retry_after = retry_after

    def json(self):
        if self.status_code == 429:
            return {'ok': False, 'parameters': {'retry_after': self.retry_after}}
        return {'ok': True}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError('synthetic HTTP failure', response=self)


def queued():
    con = common.connect()
    text = 'A' * telegram.MAX_LEN + '\nB\n' + 'C' * telegram.MAX_LEN
    con.execute('INSERT INTO pulse_queue VALUES (1, ?, ?, ?)',
                (text, json.dumps(['agent']), 'test-chat'))
    con.commit()
    return con, text


def deliver(con):
    return report.deliver_pulse(con, common.DEFAULTS,
                                {'TELEGRAM_TOKEN': 'test-token', 'TELEGRAM_CHAT_ID': 'test-chat'})


@pytest.mark.parametrize('lost_response', [False, True])
def test_restart_retries_only_unconfirmed_chunk(monkeypatch, lost_response):
    con, text = queued()
    chunks = telegram.split_message(text)
    accepted = []
    attempts = []

    def post(*a, **k):
        chunk = k['json']['text']
        attempts.append(chunk)
        if len(attempts) == 2:
            if lost_response:
                accepted.append(chunk)  # Telegram applied sendMessage; reply was lost.
                raise requests.ReadTimeout('synthetic lost response')
            return Response(500)
        accepted.append(chunk)
        return Response()

    monkeypatch.setattr(requests, 'post', post)
    with pytest.raises(requests.RequestException):
        deliver(con)
    assert con.execute('SELECT count(*) FROM pulse_alerts').fetchone()[0] == 0
    con.close()
    con = common.connect()
    assert deliver(con) == text
    assert attempts == [chunks[0], chunks[1], chunks[1], chunks[2]]
    # Existing at-least-once policy: only the unconfirmed chunk may duplicate.
    assert accepted == ([chunks[0], chunks[1], chunks[1], chunks[2]] if lost_response else chunks)
    assert con.execute('SELECT count(*) FROM pulse_queue').fetchone()[0] == 0
    assert con.execute('SELECT count(*) FROM pulse_alerts').fetchone()[0] == 1


def test_retry_after_survives_restart_and_expires_at_boundary(monkeypatch):
    Clock.instant = datetime(2026, 10, 21, tzinfo=timezone.utc)
    monkeypatch.setattr(common, 'datetime', Clock)
    con, text = queued()
    chunks = telegram.split_message(text)
    attempts = []

    def post(*a, **k):
        attempts.append(k['json']['text'])
        return Response(429, 60) if len(attempts) == 2 else Response()

    monkeypatch.setattr(requests, 'post', post)
    with pytest.raises(requests.HTTPError):
        deliver(con)
    con.close()
    con = common.connect()
    Clock.instant += timedelta(seconds=59)
    deliver(con)
    assert attempts == chunks[:2]
    assert con.execute('SELECT count(*) FROM pulse_alerts').fetchone()[0] == 0
    Clock.instant += timedelta(seconds=1)
    assert deliver(con) == text
    assert attempts == [chunks[0], chunks[1], chunks[1], chunks[2]]


@pytest.mark.parametrize('delay', [None, -1, '60', True])
def test_invalid_retry_after_keeps_pending_part_retryable(monkeypatch, delay):
    con, text = queued()
    calls = []

    def post(*a, **k):
        calls.append(k['json']['text'])
        return Response(429, delay) if len(calls) == 1 else Response()

    monkeypatch.setattr(requests, 'post', post)
    with pytest.raises(requests.HTTPError):
        deliver(con)
    assert deliver(con) == text
    chunks = telegram.split_message(text)
    assert calls == [chunks[0]] + chunks


def test_cooldown_failure_after_delivery_does_not_repeat_confirmed_parts(monkeypatch):
    import sqlite3
    con, text = queued()
    calls = []

    def post(*a, **k):
        calls.append(k['json']['text'])
        return Response()

    monkeypatch.setattr(requests, 'post', post)
    con.execute("CREATE TRIGGER fail_alert BEFORE INSERT ON pulse_alerts "
                "BEGIN SELECT RAISE(ABORT, 'synthetic storage failure'); END")
    con.commit()
    with pytest.raises(sqlite3.IntegrityError):
        deliver(con)
    con.close()
    con = common.connect()
    con.execute('DROP TRIGGER fail_alert')
    con.commit()
    assert deliver(con) == text
    assert calls == telegram.split_message(text)
    assert con.execute('SELECT count(*) FROM pulse_delivery').fetchone()[0] == 0
