"""Отчёты rssradar: пульс (всплеск объёма), недельный дайджест, карта."""
import argparse
import sys
from datetime import timedelta

import common


def term_links(con, term, since_day, limit=3):
    rows = con.execute(
        "SELECT title, link FROM entries WHERE first_seen >= ? "
        "ORDER BY first_seen DESC", (since_day,)
    ).fetchall()
    out = []
    for r in rows:
        if term in common.normalize_terms(r["title"]):
            out.append((r["title"], r["link"]))
        if len(out) >= limit:
            break
    return out


def pulse_signals(con, cfg, now=None):
    now = now or common.now_utc()
    if common.data_span_days(con, now) < cfg["pulse_baseline_days"]:
        return []                                  # базовой линии ещё нет
    window_start = (now - timedelta(days=cfg["pulse_window_days"])).strftime("%Y-%m-%d")
    baseline_start = (now - timedelta(days=cfg["pulse_baseline_days"]
                                      + cfg["pulse_window_days"])).strftime("%Y-%m-%d")
    win_days = max(cfg["pulse_window_days"], 1)
    base_days = max(cfg["pulse_baseline_days"], 1)
    signals = []
    terms = [r["term"] for r in con.execute(
        "SELECT DISTINCT term FROM term_counts WHERE day >= ?", (window_start,))]
    for term in terms:
        recent_feeds = con.execute(
            "SELECT COUNT(DISTINCT feed_url) FROM term_counts "
            "WHERE term = ? AND day >= ?", (term, window_start)).fetchone()[0]
        if recent_feeds < cfg["pulse_min_feeds"]:
            continue
        recent_rows = con.execute(
            "SELECT COUNT(*) FROM term_counts WHERE term = ? AND day >= ?",
            (term, window_start)).fetchone()[0]
        base_rows = con.execute(
            "SELECT COUNT(*) FROM term_counts "
            "WHERE term = ? AND day >= ? AND day < ?",
            (term, baseline_start, window_start)).fetchone()[0]
        recent_daily = recent_rows / win_days
        baseline_daily = base_rows / base_days
        ratio = recent_daily / baseline_daily if baseline_daily else float("inf")
        if ratio < cfg["pulse_spike_multiplier"]:
            continue
        if common.was_alerted_recently(con, term, now, cfg["pulse_cooldown_days"]):
            continue
        signals.append({
            "term": term, "recent_feeds": recent_feeds,
            "recent_daily": recent_daily, "baseline_daily": baseline_daily,
            "ratio": ratio,
            "links": term_links(con, term, window_start),
        })
    signals.sort(key=lambda s: (s["recent_feeds"], s["ratio"]), reverse=True)
    return signals


def format_pulse(signals):
    lines = ["🔥 RSS-пульс: темы набирают обороты", ""]
    for s in signals:
        mult = "∞" if s["ratio"] == float("inf") else "×{:.1f}".format(s["ratio"])
        lines.append("• {} — {} к базовой линии, в {} источниках".format(
            s["term"], mult, s["recent_feeds"]))
        for title, link in s["links"]:
            lines.append("  {}".format(link))
        lines.append("")
    return "\n".join(lines).strip()
