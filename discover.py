"""Поиск RSS/Atom-фидов по URL сайта и их валидация."""
import re
from urllib.parse import urljoin, urlparse

import feedparser
import requests

_LINK_RE = re.compile(
    r'<link[^>]+rel=["\']alternate["\'][^>]*>', re.IGNORECASE)
_HREF_RE = re.compile(r'href=["\']([^"\']+)["\']', re.IGNORECASE)
_TYPE_RE = re.compile(r'type=["\']([^"\']+)["\']', re.IGNORECASE)
_COMMON_PATHS = ["/feed", "/rss", "/feed.xml", "/rss.xml", "/atom.xml", "/index.xml"]


def feed_links_in_html(base_url, html):
    found = []
    for tag in _LINK_RE.findall(html or ""):
        tm = _TYPE_RE.search(tag)
        if tm and ("rss" in tm.group(1).lower() or "atom" in tm.group(1).lower()):
            hm = _HREF_RE.search(tag)
            if hm:
                found.append(urljoin(base_url, hm.group(1)))
    root = "{}://{}".format(urlparse(base_url).scheme, urlparse(base_url).netloc)
    for p in _COMMON_PATHS:
        found.append(root + p)
    seen, unique = set(), []
    for u in found:
        if u not in seen:
            seen.add(u)
            unique.append(u)
    return unique


def is_valid_feed(url):
    try:
        parsed = feedparser.parse(url)
    except Exception:                       # noqa: BLE001
        return False
    return bool(parsed.entries) and not (getattr(parsed, "bozo", 0) and not parsed.entries)


def discover(site_url):
    feeds, errors = [], {}
    try:
        resp = requests.get(site_url, timeout=20,
                            headers={"User-Agent": "rssradar/1.0"})
        resp.raise_for_status()
        html = resp.text
    except requests.RequestException as exc:
        html = ""
        errors[site_url] = "сайт недоступен: {}".format(exc)
    for candidate in feed_links_in_html(site_url, html):
        if candidate in feeds:
            continue
        if is_valid_feed(candidate):
            feeds.append(candidate)
    if not feeds and site_url not in errors:
        errors[site_url] = "фид не найден"
    return feeds, errors
