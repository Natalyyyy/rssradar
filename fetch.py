"""Сбор RSS/Atom-фидов через feedparser → SQLite (+ частоты терминов)."""
import argparse
import sys
from calendar import timegm
from datetime import datetime, timezone

import feedparser
import requests

import common

_USER_AGENT = {"User-Agent": "rssradar/1.0"}
_TIMEOUT = 30


def _entry_date(e):
    t = e.get("published_parsed") or e.get("updated_parsed")
    if not t:
        return ""
    return datetime.fromtimestamp(timegm(t), tz=timezone.utc).isoformat()


def entries_from_parsed(feed_url, parsed):
    records = []
    for e in parsed.entries:
        entry_id = e.get("id") or e.get("link") or e.get("title") or ""
        if not entry_id:
            continue
        records.append({
            "feed_url": feed_url,
            "entry_id": entry_id,
            "title": e.get("title", ""),
            "link": e.get("link", ""),
            "published": _entry_date(e),
            "summary": e.get("summary", ""),
        })
    return records


def _parse(url, etag, modified):
    """Качает фид через requests (таймаут + HTTP-статус — feedparser.parse(url)
    сам таймаута не имеет и может зависнуть навсегда), затем парсит content.
    Отдельная функция — чтобы мокать в тестах."""
    headers = dict(_USER_AGENT)
    if etag:
        headers["If-None-Match"] = etag
    if modified:
        headers["If-Modified-Since"] = modified
    resp = requests.get(url, timeout=_TIMEOUT, headers=headers)
    if resp.status_code == 304:
        parsed = feedparser.FeedParserDict()
        parsed["bozo"] = 0
        parsed["entries"] = []
    else:
        parsed = feedparser.parse(resp.content)
    parsed["status"] = resp.status_code
    parsed["etag"] = resp.headers.get("ETag") or etag
    parsed["modified"] = resp.headers.get("Last-Modified") or modified
    return parsed


def fetch_feed(con, feed_url, commit=True):
    etag, modified = common.get_feed_cache(con, feed_url)
    parsed = _parse(feed_url, etag, modified)
    status = getattr(parsed, "status", None)
    if status == 304:                      # не менялся
        common.set_feed_status(con, feed_url,
                               etag=etag, modified=modified, commit=commit)
        return [], None
    if status is not None and status >= 400:
        # HTTP-ошибка: тело (даже если распарсилось) не считаем валидным фидом,
        # иначе 404-страница с XML-подобным телом маскируется под last_ok.
        return [], "HTTP {}".format(status)
    if getattr(parsed, "bozo", 0) and not parsed.entries:
        return [], "не удалось разобрать фид"
    records = entries_from_parsed(feed_url, parsed)
    common.set_feed_status(con, feed_url,
                           etag=getattr(parsed, "etag", None),
                           modified=getattr(parsed, "modified", None), commit=commit)
    return records, None


def run_fetch(cfg=None, con=None, limit=None):
    cfg = cfg or common.load_config()
    con = con or common.connect()
    n_new, errors = 0, {}
    for feed_url in cfg["feeds"]:
        try:
            with con:
                # Дата наступает независимо от HTTP-статуса, ротации фида
                # и лимита новых записей. Повторный учёт идемпотентен.
                deferred = con.execute(
                    "SELECT * FROM entries WHERE feed_url=? "
                    "AND substr(published, 1, 10) > substr(first_seen, 1, 10) "
                    "AND substr(published, 1, 10) <= ?",
                    (feed_url, common.day_of(common.now_utc())),
                ).fetchall()
                common.record_terms(con, [dict(r) for r in deferred], commit=False)
            with con:
                records, err = fetch_feed(con, feed_url, commit=False)
                if err:
                    raise ValueError(err)
                if limit:
                    records = records[:limit]
                added = common.save_entries(con, records, commit=False)
                common.record_terms(con, records, commit=False)
            n_new += added
        except Exception as exc:           # noqa: BLE001 — сеть/парсер/база
            errors[feed_url] = str(exc)
            common.set_feed_status(con, feed_url, error=str(exc))
    return n_new, errors


def collect(cfg=None, con=None, limit=None):
    """Полный сбор: RSS-фиды + сайты без RSS (Anthropic). Точка входа для
    fetch.py и report.py — run_fetch остаётся чисто про фиды."""
    cfg = cfg or common.load_config()
    con = con or common.connect()
    n_new, errors = run_fetch(cfg=cfg, con=con, limit=limit)
    import scrape_sites
    s_new, s_errors = scrape_sites.run_scrape(con=con, limit=limit)
    return n_new + s_new, {**errors, **s_errors}


def main():
    ap = argparse.ArgumentParser(description="Сбор RSS-фидов в базу rssradar")
    ap.add_argument("--limit", type=int, default=None,
                    help="ограничить число записей на фид (холодный старт, напр. 30)")
    args = ap.parse_args()
    try:
        cfg = common.load_config()
    except common.ConfigError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    n_new, errors = collect(cfg=cfg, limit=args.limit)
    print("Новых записей: {}".format(n_new))
    for feed, e in errors.items():
        print("⚠️ {}: {}".format(feed, e), file=sys.stderr)


if __name__ == "__main__":
    main()
