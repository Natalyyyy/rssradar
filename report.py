"""Отчёты rssradar: пульс (всплеск объёма), недельный дайджест, карта."""
import argparse
import html as html_lib
import json
import subprocess
import sys
import webbrowser
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
    try:
        prompt = Path(prompt_path).read_text(encoding="utf-8") + "\n\n" + "\n".join(digest)
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


def _clean_clusters(raw):
    """Приводит распарсенный JSON от Claude к списку валидных кластеров или None."""
    if not isinstance(raw, list):
        return None
    clusters = []
    for c in raw:
        if not isinstance(c, dict):
            continue
        theme = c.get("theme")
        theme = str(theme) if theme is not None else ""
        try:
            heat = int(c.get("heat", 1))
        except (TypeError, ValueError):
            heat = 1
        items = []
        raw_items = c.get("items")
        if isinstance(raw_items, list):
            for it in raw_items:
                if not isinstance(it, dict):
                    continue
                items.append({"title": str(it.get("title") or ""),
                              "link": str(it.get("link") or "")})
        clusters.append({"theme": theme, "heat": heat, "items": items})
    return clusters or None


def claude_clusters(entries, prompt_path):
    """Тематические кластеры недели (JSON) через headless Claude. Любой сбой → None."""
    if not entries:
        return None
    digest = ["- {} — {}".format(" ".join((e["title"] or "").split()), e["link"])
              for e in entries]
    try:
        prompt = Path(prompt_path).read_text(encoding="utf-8") + "\n\n" + "\n".join(digest)
        result = subprocess.run(
            ["claude", "-p", "--output-format", "text"],
            input=prompt, capture_output=True, text=True, timeout=300,
        )
        if result.returncode == 0 and result.stdout.strip():
            raw = result.stdout.strip()
            start, end = raw.find("["), raw.rfind("]")
            if start != -1 and end != -1:
                return _clean_clusters(json.loads(raw[start:end + 1]))
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        pass
    return None


def _safe_href(link):
    return link if str(link or "").lower().startswith(("http://", "https://")) else ""


def render_map_html(clusters, title):
    esc = html_lib.escape
    if not clusters:
        body = "<p class='empty'>Пока нет данных для карты.</p>"
    else:
        cards = []
        for c in clusters:
            heat = max(1, min(int(c.get("heat", 1)), 6))
            item_htmls = []
            for i in c.get("items", []):
                href = _safe_href(i.get("link", ""))
                title_html = esc(i.get("title", ""))
                if href:
                    item_htmls.append("<li><a href='{}'>{}</a></li>".format(
                        esc(href), title_html))
                else:
                    item_htmls.append("<li>{}</li>".format(title_html))
            items = "".join(item_htmls)
            cards.append(
                "<section class='card heat-{h}'><h2>{t}</h2><ul>{i}</ul></section>".format(
                    h=heat, t=esc(c.get("theme", "")), i=items))
        body = "<div class='grid'>" + "".join(cards) + "</div>"
    return """<!doctype html><html lang='ru'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width, initial-scale=1'>
<title>{title}</title><style>
:root{{color-scheme:light dark}}
body{{font:16px/1.5 -apple-system,system-ui,sans-serif;margin:2rem;max-width:1100px}}
h1{{font-size:1.4rem}}
.grid{{display:flex;flex-wrap:wrap;gap:1rem;align-items:flex-start}}
.card{{border:1px solid color-mix(in srgb, currentColor 20%, transparent);
  border-radius:12px;padding:1rem;flex:1 1 240px}}
.card h2{{margin:.2rem 0 .6rem;font-size:1.05rem}}
.heat-6{{flex-basis:420px}} .heat-5{{flex-basis:360px}} .heat-4{{flex-basis:320px}}
.heat-3{{flex-basis:280px}} .heat-2{{flex-basis:240px}} .heat-1{{flex-basis:220px}}
.heat-6 h2::before,.heat-5 h2::before,.heat-4 h2::before{{content:'🔥 '}}
ul{{margin:0;padding-left:1.1rem}} a{{color:inherit}}
.empty{{opacity:.6}}
</style></head><body><h1>{title}</h1>{body}</body></html>""".format(
        title=esc(title), body=body)


def write_and_open(html):
    common.DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = common.DATA_DIR / "map-{}.html".format(common.day_of(common.now_utc()))
    path.write_text(html, encoding="utf-8")
    try:
        webbrowser.open(path.as_uri())
    except Exception:                          # noqa: BLE001 — headless-среда
        pass
    return path


def build_map(con, cfg, now=None):
    entries = weekly_entries(con, cfg, now=now)
    clusters = claude_clusters(entries, common.ROOT / "prompts" / "map.md")
    if clusters is None:                       # фолбэк: одна тема-свалка
        clusters = [{"theme": "Новое за неделю", "heat": 3,
                     "items": [{"title": e["title"], "link": e["link"]}
                               for e in entries]}] if entries else []
    html = render_map_html(clusters, "RSS-карта трендов")
    return str(write_and_open(html))


FETCH_ERROR_MSG = (
    "⚠️ rssradar: не удалось собрать фиды (сеть или битый фид). "
    "Проверь список в config.json.")


def main():
    ap = argparse.ArgumentParser(description="Отчёты rssradar")
    ap.add_argument("mode", choices=["pulse", "weekly", "map"])
    ap.add_argument("--send", action="store_true", help="отправить в Telegram")
    ap.add_argument("--no-fetch", action="store_true", help="только по данным из базы")
    args = ap.parse_args()

    try:
        cfg = common.load_config()
    except common.ConfigError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    env, con = common.load_env(), common.connect()

    if not args.no_fetch:
        import fetch
        try:
            _n_new, errors = fetch.run_fetch(cfg=cfg, con=con)
            for feed, err in errors.items():
                print("⚠️ {}: {}".format(feed, err), file=sys.stderr)
        except Exception as exc:                       # noqa: BLE001
            print("{} ({})".format(FETCH_ERROR_MSG, exc), file=sys.stderr)

    if args.mode == "pulse":
        signals = pulse_signals(con, cfg)
        if not signals:
            print("Тихо: всплесков нет.")
            return
        text = format_pulse(signals)
        common.mark_alerted(con, [s["term"] for s in signals])
    elif args.mode == "weekly":
        text = build_weekly(con, cfg)
    else:
        path = build_map(con, cfg)
        text = "🗺 Карта трендов собрана: {}".format(path)

    print(text)
    if args.send and env.get("TELEGRAM_TOKEN") and env.get("TELEGRAM_CHAT_ID"):
        import telegram
        telegram.send(text, env["TELEGRAM_TOKEN"], env["TELEGRAM_CHAT_ID"])


if __name__ == "__main__":
    main()
