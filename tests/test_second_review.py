from datetime import datetime, timedelta, timezone
import sys

import pytest
import requests

import common
import report
import scrape_sites
import telegram
from tests.test_round2 import Clock, Response


@pytest.mark.parametrize('later', ['rotated', 'empty', 'offline'])
def test_scraper_restores_due_article_without_card(monkeypatch, later):
    Clock.instant = datetime(2026, 10, 21, tzinfo=timezone.utc)
    monkeypatch.setattr(common, 'datetime', Clock)
    monkeypatch.setattr(scrape_sites, 'SITES', [('scrape:anthropic/news', 'https://example.test/news', 'news')])
    class Page(Response):
        text = '<a href="/news/future"><h2>agent</h2><time>Oct 22, 2026</time></a>'
    monkeypatch.setattr(requests, 'get', lambda *a, **k: Page())
    con = common.connect()
    assert scrape_sites.run_scrape(con)[0] == 1
    assert con.execute('SELECT count(*) FROM term_counts').fetchone()[0] == 0
    Clock.instant += timedelta(days=1)
    def get(*a, **k):
        if later == 'offline':
            raise requests.ConnectionError('synthetic offline')
        page = Page()
        page.text = '' if later == 'empty' else '<a href="/news/other"><h2>robot</h2></a>'
        return page
    monkeypatch.setattr(requests, 'get', get)
    for _ in range(2):
        scrape_sites.run_scrape(con, limit=1)
        assert [tuple(r) for r in con.execute("SELECT * FROM term_counts WHERE term='agent'")] == [
            ('agent', '2026-10-22', 'scrape:anthropic/news')]


def weekly_setup(monkeypatch):
    # Only isolated fixture paths are used; no real settings or credentials.
    env_file = common.DATA_DIR / 'test-env.txt'
    monkeypatch.setattr(common, 'ENV_PATH', env_file)
    env_file.write_text('TELEGRAM_TOKEN=test-token\nTELEGRAM_CHAT_ID=test-chat\n')
    monkeypatch.setattr(report.shutil, 'which', lambda name: None)
    con = common.connect()
    common.save_entries(con, [dict(feed_url='feed', entry_id=str(i),
        title=('agent ' + str(i) + ' ' + 'x' * 200), link='https://example.test/' + str(i))
        for i in range(45)])
    text = report.build_weekly(con, common.DEFAULTS) + report.format_failed_feeds(con)
    con.close()
    assert len(telegram.split_message(text)) >= 3
    monkeypatch.setattr(sys, 'argv', ['report.py', 'weekly', '--send', '--no-fetch'])
    return text


@pytest.mark.parametrize('lost_response', [False, True])
def test_weekly_restart_preserves_payload_and_confirmed_parts(monkeypatch, lost_response):
    text = weekly_setup(monkeypatch)
    chunks = telegram.split_message(text)
    attempts, accepted = [], []
    def post(*a, **k):
        chunk = k['json']['text']
        attempts.append(chunk)
        if len(attempts) == 2:
            if lost_response:
                accepted.append(chunk)
                raise requests.ReadTimeout('synthetic lost response')
            return Response(500)
        accepted.append(chunk)
        return Response()
    monkeypatch.setattr(requests, 'post', post)
    with pytest.raises(requests.RequestException):
        report.main()
    con = common.connect()
    con.execute('DELETE FROM entries')
    con.commit()
    con.close()
    report.main()
    assert attempts == chunks[:2] + chunks[1:]
    assert accepted == (chunks[:2] + chunks[1:] if lost_response else chunks)


def test_weekly_retry_after_persists_until_exact_boundary(monkeypatch):
    Clock.instant = datetime(2026, 10, 21, tzinfo=timezone.utc)
    monkeypatch.setattr(common, 'datetime', Clock)
    chunks = telegram.split_message(weekly_setup(monkeypatch))
    attempts = []
    def post(*a, **k):
        attempts.append(k['json']['text'])
        return Response(429, 60) if len(attempts) == 2 else Response()
    monkeypatch.setattr(requests, 'post', post)
    with pytest.raises(requests.HTTPError):
        report.main()
    Clock.instant += timedelta(seconds=59)
    report.main()
    assert attempts == chunks[:2]
    Clock.instant += timedelta(seconds=1)
    report.main()
    assert attempts == chunks[:2] + chunks[1:]


def test_weekly_pending_recipient_cannot_change(monkeypatch):
    weekly_setup(monkeypatch)
    attempts = []
    def post(*a, **k):
        attempts.append(k['json']['text'])
        raise requests.ReadTimeout('synthetic lost response')
    monkeypatch.setattr(requests, 'post', post)
    with pytest.raises(requests.ReadTimeout):
        report.main()
    common.ENV_PATH.write_text('TELEGRAM_TOKEN=test-token\nTELEGRAM_CHAT_ID=other-chat\n')
    with pytest.raises(ValueError, match='Получатель'):
        report.main()
    assert len(attempts) == 1


def test_weekly_success_clears_queue_and_preview_does_not_send(monkeypatch):
    text = weekly_setup(monkeypatch)
    attempts = []
    def post(*a, **k):
        attempts.append(k['json']['text'])
        return Response()
    monkeypatch.setattr(requests, 'post', post)
    report.main()
    assert attempts == telegram.split_message(text)
    con = common.connect()
    assert con.execute('SELECT count(*) FROM weekly_delivery').fetchone()[0] == 0
    con.close()
    monkeypatch.setattr(sys, 'argv', ['report.py', 'weekly', '--no-fetch'])
    report.main()
    assert attempts == telegram.split_message(text)


def test_weekly_parallel_sender_cannot_repeat_parts(monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    text = weekly_setup(monkeypatch)
    entered, release = threading.Event(), threading.Event()
    attempts = []
    def post(*a, **k):
        attempts.append(k['json']['text'])
        entered.set()
        assert release.wait(5)
        return Response()
    monkeypatch.setattr(requests, 'post', post)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(report.main)
        assert entered.wait(5)
        try:
            pool.submit(report.main).result(timeout=3)
        finally:
            release.set()
        first.result(timeout=3)
    assert attempts == telegram.split_message(text)
