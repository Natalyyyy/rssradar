"""Отчёты rssradar: пульс (всплеск объёма), недельный дайджест, карта."""
import argparse
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

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
    win_days = max(cfg["pulse_window_days"], 1)
    base_days = max(cfg["pulse_baseline_days"], 1)
    window_start = (now - timedelta(days=win_days - 1)).strftime("%Y-%m-%d")
    baseline_start = (now - timedelta(days=win_days - 1 + base_days)).strftime("%Y-%m-%d")
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


def _norm_link(link):
    return (link or "").split("?")[0].rstrip("/").lower()


def weekly_entries(con, cfg, now=None):
    now = now or common.now_utc()
    since = (now - timedelta(days=cfg["weekly_window_days"])).isoformat()
    rows = con.execute(
        "SELECT feed_url, title, link, published, summary, first_seen "
        "FROM entries WHERE first_seen >= ? ORDER BY first_seen DESC", (since,)
    ).fetchall()
    seen, out = set(), []
    for r in rows:
        key = _norm_link(r["link"])
        if key and key in seen:
            continue
        seen.add(key)
        out.append(dict(r))
    return out


def claude_summary(entries, prompt_path):
    """Тематический дайджест недели через headless Claude. Любой сбой → None."""
    if not entries:
        return None
    digest = []
    for e in entries:
        title = " ".join((e["title"] or "").split())
        digest.append("- {} — {}".format(title, e["link"]))
    prompt = Path(prompt_path).read_text(encoding="utf-8") + "\n\n" + "\n".join(digest)
    try:
        result = subprocess.run(
            ["claude", "-p", "--output-format", "text"],
            input=prompt, capture_output=True, text=True, timeout=300,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def build_weekly(con, cfg, now=None):
    entries = weekly_entries(con, cfg, now=now)
    header = "📰 RSS за неделю"
    if not entries:
        return header + "\n\nЗа неделю новых статей не появилось."
    summary = claude_summary(entries, common.ROOT / "prompts" / "weekly.md")
    if summary:
        return "{}\n\n{}".format(header, summary)
    lines = [header, "", "Новое за неделю:"]           # фолбэк без Claude
    for e in entries:
        title = " ".join((e["title"] or "").split())
        lines.append("• {} — {}".format(title, e["link"]))
    return "\n".join(lines).strip()
