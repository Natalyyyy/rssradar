"""Second review: offline boundaries, persistent delivery and deferred dates."""
from datetime import datetime, timedelta, timezone
import sqlite3

import pytest
import requests

import common
import fetch
import report
import telegram
from tests.test_fetch import FakeResponse
from tests.test_round2 import Clock, Response


@pytest.mark.parametrize('failure', ['http500', 'timeout', 'malformed'])
def test_due_rss_terms_are_counted_even_when_fetch_fails(monkeypatch, failure):
    Clock.instant = datetime(2026, 10, 21, tzinfo=timezone.utc)
    monkeypatch.setattr(common, 'datetime', Clock)
    con = common.connect()
    common.save_entries(con, [dict(feed_url='feed', entry_id='future', title='agent',
                                  published='2026-10-22T00:00:00+00:00')])
    common.set_feed_status(con, 'feed', etag='cached')
    def get(*args, **kwargs):
        if failure == 'timeout':
            raise requests.ReadTimeout('synthetic timeout')
        return FakeResponse(500 if failure == 'http500' else 200, b'broken xml')
    monkeypatch.setattr(requests, 'get', get)
    fetch.run_fetch({'feeds': ['feed']}, con)
    assert con.execute('SELECT count(*) FROM term_counts').fetchone()[0] == 0
    Clock.instant += timedelta(days=1)
    for _ in range(2):
        count, errors = fetch.run_fetch({'feeds': ['feed']}, con)
        assert count == 0 and 'feed' in errors
        assert common.get_feed_cache(con, 'feed')[0] == 'cached'
        assert [tuple(row) for row in con.execute('SELECT * FROM term_counts')] == [
            ('agent', '2026-10-22', 'feed')]


@pytest.mark.parametrize('lost_response', [False, True])
@pytest.mark.parametrize('fail_current', [False, True])
def test_old_weekly_retry_also_delivers_current_week(monkeypatch, lost_response, fail_current):
    Clock.instant = datetime(2026, 10, 21, tzinfo=timezone.utc)
    monkeypatch.setattr(common, 'datetime', Clock)
    monkeypatch.setattr(report.shutil, 'which', lambda name: None)
    con = common.connect()
    env = {'TELEGRAM_TOKEN': 'test-token', 'TELEGRAM_CHAT_ID': 'test-chat'}
    common.save_entries(con, [dict(feed_url='feed', entry_id='old', title='old agent',
                                  link='https://example.test/old')])
    old = telegram.split_message(report.build_weekly(con, common.DEFAULTS))
    attempts, accepted = [], []
    def post(*args, **kwargs):
        chunk = kwargs['json']['text']
        attempts.append(chunk)
        if len(attempts) == 1 or (fail_current and len(attempts) == 3):
            if lost_response:
                accepted.append(chunk)
                raise requests.ReadTimeout('synthetic lost response')
            return Response(500)
        accepted.append(chunk)
        return Response()
    monkeypatch.setattr(requests, 'post', post)
    with pytest.raises(requests.RequestException):
        report.deliver_weekly(con, common.DEFAULTS, env)
    con.close()
    Clock.instant += timedelta(days=7)
    con = common.connect()
    common.save_entries(con, [dict(feed_url='feed', entry_id='current', title='current robot',
                                  link='https://example.test/current')])
    current = telegram.split_message(report.build_weekly(con, common.DEFAULTS))
    if fail_current:
        with pytest.raises(requests.RequestException):
            report.deliver_weekly(con, common.DEFAULTS, env)
        con.close()
        con = common.connect()
    report.deliver_weekly(con, common.DEFAULTS, env)
    assert attempts == old + old + current * (2 if fail_current else 1)
    assert accepted == old * (2 if lost_response else 1) + current * (
        2 if fail_current and lost_response else 1)
    assert con.execute('SELECT count(*) FROM weekly_delivery').fetchone()[0] == 0


def test_legacy_weekly_queue_migration_preserves_pending_payload(tmp_path):
    path = tmp_path / 'legacy.db'
    con = sqlite3.connect(path)
    con.execute('CREATE TABLE weekly_delivery (id INTEGER PRIMARY KEY, text TEXT, '
                'chat_id TEXT, chunks TEXT, next_part INTEGER DEFAULT 0, retry_at TEXT)')
    con.execute("INSERT INTO weekly_delivery VALUES (1, 'old', 'test-chat', '[\"old\"]', 0, NULL)")
    con.commit()
    con.close()
    for _ in range(2):
        con = common.connect(path)
        row = con.execute('SELECT * FROM weekly_delivery').fetchone()
        assert row['text'] == 'old'
        assert row['created_at'] is None
        con.close()
