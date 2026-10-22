"""Сайты без RSS → те же entries в базе rssradar.

Anthropic (news / engineering / research) фида не отдаёт, а это главный
первоисточник ниши. Тянем карточки со страниц-индексов и кладём в ту же
таблицу entries, что и обычные фиды — report.py их не отличает.

Запуск: python3 scrape_sites.py [--limit 30]
"""
from __future__ import annotations

import argparse
import html as html_mod
import re
import sys
from html.parser import HTMLParser
from datetime import datetime, timezone

import requests

import common

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120 Safari/537.36"}
TIMEOUT = 30

SITES = [
    ("scrape:anthropic/news",        "https://www.anthropic.com/news",        "news"),
    ("scrape:anthropic/engineering", "https://www.anthropic.com/engineering", "engineering"),
    ("scrape:anthropic/research",    "https://www.anthropic.com/research",    "research"),
]

# заголовок карточки — либо <h1>..<h6>, либо <span class="...__title ...">
TITLE_RE = re.compile(
    r"<h[1-6][^>]*>(.*?)</h[1-6]>|<span[^>]*__title[^>]*>(.*?)</span>", re.DOTALL)
DATE_RE = re.compile(r"(?:<time[^>]*>|__date[^\"]*\">)([A-Z][a-z]{2}\s+\d{1,2},\s+\d{4})<")
TAG_RE = re.compile(r"<[^>]+>")


def _text(raw: str) -> str:
    return html_mod.unescape(TAG_RE.sub("", raw)).strip()


def _iso(date_str: str) -> str:
    try:
        return datetime.strptime(date_str, "%b %d, %Y").replace(tzinfo=timezone.utc).isoformat()
    except ValueError:
        return ""


class _Cards(HTMLParser):
    """Содержимое каждой ссылки отдельно: соседние карточки не смешиваются."""
    def __init__(self):
        super().__init__()
        self.cards = []
        self.href = None
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.href = dict(attrs).get("href")
            self.parts = []
        elif self.href is not None:
            self.parts.append(self.get_starttag_text())

    def handle_data(self, data):
        if self.href is not None:
            self.parts.append(html_mod.escape(data))

    def handle_endtag(self, tag):
        if tag == "a":
            if self.href is not None:
                self.cards.append((self.href, "".join(self.parts)))
            self.href = None
            self.parts = []
        elif self.href is not None:
            self.parts.append("</%s>" % tag)


def parse_index(html: str, section: str, base="https://www.anthropic.com") -> list[dict]:
    """Карточки со страницы-индекса: заголовок и дата внутри ссылки."""
    out, seen = [], set()
    parser = _Cards()
    parser.feed(html)
    for slug, tail in parser.cards:
        if not re.fullmatch(r"/%s/[a-z0-9\-]+" % re.escape(section), slug):
            continue
        if slug in seen:
            continue
        tm = TITLE_RE.search(tail)
        if not tm:
            continue
        title = _text(tm.group(1) or tm.group(2) or "")
        if not title:
            continue
        seen.add(slug)
        dm = DATE_RE.search(tail)
        out.append({
            "feed_url": "scrape:anthropic/%s" % section,
            "entry_id": base + slug,
            "title": title,
            "link": base + slug,
            "published": _iso(dm.group(1)) if dm else "",
            "summary": "",
        })
    return out


def run_scrape(con=None, limit=None):
    """Скрейп всех SITES в базу. Возвращает (новых записей, {feed_url: ошибка})."""
    con = con or common.connect()
    n_new, errors = 0, {}
    for feed_url, url, section in SITES:
        try:
            # Дата сохранённой статьи наступает независимо от доступности
            # индекса, ротации карточек и лимита новых записей.
            with con:
                saved = con.execute(
                    "SELECT * FROM entries WHERE feed_url=?", (feed_url,)
                ).fetchall()
                common.record_terms(con, [dict(row) for row in saved], commit=False)
            resp = requests.get(url, timeout=TIMEOUT, headers=UA)
            resp.raise_for_status()
            records = parse_index(resp.text, section)
        except Exception as exc:                       # noqa: BLE001 — сеть/парсер
            errors[feed_url] = str(exc)
            common.set_feed_status(con, feed_url, error=str(exc))
            continue
        if not records:                                # разметка страницы поехала
            errors[feed_url] = "карточки не распарсились"
            common.set_feed_status(con, feed_url, error="карточки не распарсились")
            continue
        if limit:
            records = records[:limit]
        try:
            with con:
                added = common.save_entries(con, records, commit=False)
                common.record_terms(con, records, commit=False)
                common.set_feed_status(con, feed_url, commit=False)
            n_new += added
        except Exception as exc:                       # noqa: BLE001 — база
            errors[feed_url] = str(exc)
            common.set_feed_status(con, feed_url, error=str(exc))
    return n_new, errors


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=None,
                    help="ограничить число карточек со страницы (холодный старт, напр. 30)")
    args = ap.parse_args()
    n_new, errors = run_scrape(limit=args.limit)
    print("Новых записей: %d" % n_new)
    for feed, e in errors.items():
        print("⚠️ %s: %s" % (feed, e), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
