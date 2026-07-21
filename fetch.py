"""Сбор RSS/Atom-фидов через feedparser → SQLite (+ частоты терминов)."""
import argparse
import sys
from calendar import timegm
from datetime import datetime, timezone

import feedparser

import common


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
    """Обёртка над feedparser.parse с условным запросом. Отдельно — чтобы мокать."""
    return feedparser.parse(url, etag=etag, modified=modified)


def fetch_feed(con, feed_url):
    etag, modified = common.get_feed_cache(con, feed_url)
    parsed = _parse(feed_url, etag, modified)
    status = getattr(parsed, "status", None)
    if status == 304:                      # не менялся
        common.set_feed_status(con, feed_url,
                               etag=etag, modified=modified)
        return [], None
    if getattr(parsed, "bozo", 0) and not parsed.entries:
        return [], "не удалось разобрать фид"
    records = entries_from_parsed(feed_url, parsed)
    common.set_feed_status(con, feed_url,
                           etag=getattr(parsed, "etag", None),
                           modified=getattr(parsed, "modified", None))
    return records, None


def run_fetch(cfg=None, con=None, limit=None):
    cfg = cfg or common.load_config()
    con = con or common.connect()
    n_new, errors = 0, {}
    for feed_url in cfg["feeds"]:
        try:
            records, err = fetch_feed(con, feed_url)
        except Exception as exc:           # noqa: BLE001 — сеть/парсер
            errors[feed_url] = str(exc)
            common.set_feed_status(con, feed_url, error=str(exc))
            continue
        if err:
            errors[feed_url] = err
            common.set_feed_status(con, feed_url, error=err)
            continue
        if limit:
            records = records[:limit]
        n_new += common.save_entries(con, records)
        common.record_terms(con, records)
    return n_new, errors


def main():
    ap = argparse.ArgumentParser(description="Сбор RSS-фидов в базу rssradar")
    ap.add_argument("--limit", type=int, default=None,
                    help="ограничить число записей на фид (холодный старт, напр. 30)")
    args = ap.parse_args()
    n_new, errors = run_fetch(limit=args.limit)
    print("Новых записей: {}".format(n_new))
    for feed, e in errors.items():
        print("⚠️ {}: {}".format(feed, e), file=sys.stderr)


if __name__ == "__main__":
    main()
