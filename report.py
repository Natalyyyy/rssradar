"""Отчёты rssradar: пульс (всплеск объёма), недельный дайджест, карта."""
import os
import fcntl
import argparse
import html as html_lib
import json
import shutil
import subprocess
import sys
import webbrowser
from urllib.parse import urlsplit, urlunsplit, unquote_plus
from datetime import timedelta
from pathlib import Path

import common
import requests


# --- модель ходит без токенов (security-ревью 29.08.2026) -------------------
# В окружении прогона лежат ключи, и процесс модели наследовал их все: инъекция
# из недоверенного текста, дотянувшаяся до исполнения, забирала их одной
# строкой. Модели они не нужны — сеть за неё делают скрипты.
# CLAUDE_CODE_OAUTH_TOKEN намеренно НЕ снимаем: им логинится сам CLI.
_СЕКРЕТЫ_МИМО_МОДЕЛИ = (
    "TELEGRAM_BOT_TOKEN", "TELEGRAM_TOKEN", "TELEGRAPH_TOKEN", "TGSTAT_TOKEN",
    "APIFY_TOKEN", "IG_ACCESS_TOKEN", "GROQ_API_KEY", "HEALTH_BOT_TOKEN",
    "GCAL_SA", "DASHBOARD_AUTH", "GRANOLA_API_KEY",
)


def _env_без_секретов(base=None):
    env = dict(base if base is not None else os.environ)
    for имя in _СЕКРЕТЫ_МИМО_МОДЕЛИ:
        env.pop(имя, None)
    return env


CLAUDE_MISSING_MSG = "⚠️ claude CLI не найден в PATH — дайджест собран без Claude."


def _claude_bin():
    """Абсолютный путь к claude CLI или None. Под launchd PATH минимальный,
    поэтому зовём по голому имени 'claude' нельзя — резолвим явно."""
    return shutil.which("claude")


def term_links(con, term, since_day, limit=3, until_day=None):
    # Критерий окна — как у пульса: по published (с фолбэком на first_seen,
    # см. common.record_terms), а не по first_seen напрямую — иначе сигнал
    # может прийти с пустым списком ссылок.
    until_day = until_day or common.day_of(common.now_utc())
    rows = con.execute(
        "SELECT title, link FROM entries "
        "WHERE COALESCE(NULLIF(substr(published, 1, 10), ''), substr(first_seen, 1, 10)) BETWEEN ? AND ? "
        "ORDER BY COALESCE(NULLIF(published, ''), first_seen) DESC",
        (since_day, until_day)
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
    today = common.day_of(now)
    signals = []
    terms = [r["term"] for r in con.execute(
        "SELECT DISTINCT term FROM term_counts WHERE day BETWEEN ? AND ?", (window_start, today))]
    for term in terms:
        recent_feeds = con.execute(
            "SELECT COUNT(DISTINCT feed_url) FROM term_counts "
            "WHERE term = ? AND day BETWEEN ? AND ?", (term, window_start, today)).fetchone()[0]
        if recent_feeds < cfg["pulse_min_feeds"]:
            continue
        recent_rows = con.execute(
            "SELECT COUNT(*) FROM term_counts WHERE term = ? AND day BETWEEN ? AND ?",
            (term, window_start, today)).fetchone()[0]
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
            "links": term_links(con, term, window_start, until_day=today),
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


def format_failed_feeds(con):
    """Строка-предупреждение о фидах, не ответивших на последнем сборе.
    Пусто, если все фиды в порядке — чтобы не засорять отчёт лишний раз."""
    failed = common.failed_feeds(con)
    if not failed:
        return ""
    names = ", ".join(url for url, _ in failed)
    return "\n\n⚠️ {} фидов не ответили: {}".format(len(failed), names)


def _norm_link(link):
    try:
        parts = urlsplit(link or "")
    except ValueError:
        return link or ""
    tracking = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
                "utm_id", "gclid", "fbclid"}
    query = "&".join(part for part in parts.query.split("&")
                     if unquote_plus(part.split("=", 1)[0]).lower() not in tracking)
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path,
                       query, parts.fragment))


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


def _run_claude(claude_bin, prompt):
    # Внешние заголовки недоверенные: инструменты отключены, модель задана явно.
    return subprocess.run(
        [claude_bin, "-p", "--output-format", "text",
         "--model", "claude-opus-5",
         "--tools", "",
         "--disallowedTools", "Bash,Monitor,BashOutput,KillShell,Task,Agent,WebFetch,WebSearch,NotebookEdit,Read,Write,Edit"],
        input=prompt, env=_env_без_секретов(),
        capture_output=True, text=True, timeout=300,
    )


def claude_summary(entries, prompt_path):
    """Тематический дайджест недели через headless Claude. Любой сбой → None."""
    if not entries:
        return None
    claude_bin = _claude_bin()
    if not claude_bin:
        return None
    digest = []
    for e in entries:
        title = " ".join((e["title"] or "").split())
        digest.append("- {} — {}".format(title, e["link"]))
    try:
        prompt = Path(prompt_path).read_text(encoding="utf-8") + "\n\n" + "\n".join(digest)
        result = _run_claude(claude_bin, prompt)
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
    claude_missing = _claude_bin() is None
    summary = claude_summary(entries, common.ROOT / "prompts" / "weekly.md")
    if summary:
        return "{}\n\n{}".format(header, summary)
    lines = [header]
    if claude_missing:                                  # не молча фолбэкаем — говорим прямо
        lines.append(CLAUDE_MISSING_MSG)
    lines += ["", "Новое за неделю:"]                   # фолбэк без Claude
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
    claude_bin = _claude_bin()
    if not claude_bin:
        return None
    digest = ["- {} — {}".format(" ".join((e["title"] or "").split()), e["link"])
              for e in entries]
    try:
        prompt = Path(prompt_path).read_text(encoding="utf-8") + "\n\n" + "\n".join(digest)
        result = _run_claude(claude_bin, prompt)
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
    claude_missing = _claude_bin() is None
    clusters = claude_clusters(entries, common.ROOT / "prompts" / "map.md")
    if clusters is None:                       # фолбэк: одна тема-свалка
        theme = "Новое за неделю"
        if claude_missing and entries:         # не молча фолбэкаем — говорим прямо
            theme = "Новое за неделю (без Claude — claude CLI не найден в PATH)"
        clusters = [{"theme": theme, "heat": 3,
                     "items": [{"title": e["title"], "link": e["link"]}
                               for e in entries]}] if entries else []
    html = render_map_html(clusters, "RSS-карта трендов")
    return str(write_and_open(html))


FETCH_ERROR_MSG = (
    "⚠️ rssradar: не удалось собрать фиды (сеть или битый фид). "
    "Проверь список в config.json.")


def deliver_pulse(con, cfg, env):
    """Один отправитель на базу; очередь переживает ошибку и перезапуск."""
    import telegram
    database = con.execute("PRAGMA database_list").fetchone()[2]
    lock_path = Path(database + ".pulse.lock") if database else common.DATA_DIR / "pulse.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return "Отправка пульса уже выполняется."
        try:
            pending = con.execute("SELECT * FROM pulse_queue WHERE id=1").fetchone()
            delivered_text = None
            # После старой очереди проверяем текущее окно под тем же локом.
            # Cooldown уже доставленных терминов исключает повтор.
            while True:
                if pending:
                    if pending["chat_id"] != str(env["TELEGRAM_CHAT_ID"]):
                        raise ValueError("Получатель очереди пульса отличается от TELEGRAM_CHAT_ID")
                    text, terms = pending["text"], json.loads(pending["terms"])
                else:
                    signals = pulse_signals(con, cfg)
                    if not signals:
                        return delivered_text or ("Тихо: всплесков нет." + format_failed_feeds(con))
                    text = format_pulse(signals) + format_failed_feeds(con)
                    terms = [s["term"] for s in signals]
                    with con:
                        con.execute("INSERT INTO pulse_queue VALUES (1, ?, ?, ?)",
                                    (text, json.dumps(terms), str(env["TELEGRAM_CHAT_ID"])))
                progress = con.execute("SELECT * FROM pulse_delivery WHERE id=1").fetchone()
                if progress is None:
                    chunks = telegram.split_message(text)
                    with con:
                        con.execute("INSERT INTO pulse_delivery (id, chunks) VALUES (1, ?)",
                                    (json.dumps(chunks),))
                    next_part, retry_at = 0, None
                else:
                    chunks = json.loads(progress["chunks"])
                    next_part, retry_at = progress["next_part"], progress["retry_at"]
                if retry_at and common.now_utc() < common.parse_ts(retry_at):
                    return "Пульс в очереди: Telegram разрешит повтор после {}".format(retry_at)
                for index in range(next_part, len(chunks)):
                    try:
                        telegram.send(chunks[index], env["TELEGRAM_TOKEN"], env["TELEGRAM_CHAT_ID"])
                    except requests.RequestException as exc:
                        delay = telegram.retry_after(exc)
                        if delay is not None:
                            retry_at = (common.now_utc() + timedelta(seconds=delay)).isoformat()
                            with con:
                                con.execute("UPDATE pulse_delivery SET retry_at=? WHERE id=1",
                                            (retry_at,))
                        raise
                    # Сохраняем каждое подтверждение до следующего HTTP-запроса.
                    # Потерянный ответ остаётся неоднозначным: текущая политика
                    # повторяет только неподтверждённую часть (at-least-once).
                    with con:
                        con.execute("UPDATE pulse_delivery SET next_part=?, retry_at=NULL WHERE id=1",
                                    (index + 1,))
                with con:
                    common.mark_alerted(con, terms, commit=False)
                    con.execute("DELETE FROM pulse_queue WHERE id=1")
                    con.execute("DELETE FROM pulse_delivery WHERE id=1")
                if pending is None:
                    return text
                delivered_text = text
                pending = None
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def deliver_weekly(con, cfg, env):
    """Сохраняет исходный дайджест и подтверждения частей до следующего запуска.

    Как у пульса, потерянный ответ допускает повтор неподтверждённой части.
    Подтверждённые части повторно не отправляются.
    """
    import telegram
    database = con.execute("PRAGMA database_list").fetchone()[2]
    lock_path = Path(database + ".weekly.lock") if database else common.DATA_DIR / "weekly.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return "Отправка недельного отчёта уже выполняется."
        try:
            pending = con.execute("SELECT * FROM weekly_delivery WHERE id=1").fetchone()
            # Повтор в тот же день лишь завершает исходный отчёт. При новом
            # дне после старой очереди формируем актуальное окно под тем же локом.
            # У старых очередей без даты период неизвестен: обновляем дайджест.
            refresh = pending is not None and (
                not pending["created_at"] or
                common.day_of(common.parse_ts(pending["created_at"])) <
                common.day_of(common.now_utc()))
            while True:
                if pending is None:
                    text = build_weekly(con, cfg) + format_failed_feeds(con)
                    chunks = telegram.split_message(text)
                    with con:
                        con.execute(
                            "INSERT INTO weekly_delivery (id, text, chat_id, chunks, created_at) "
                            "VALUES (1, ?, ?, ?, ?)",
                            (text, str(env["TELEGRAM_CHAT_ID"]), json.dumps(chunks),
                             common.now_utc().isoformat()))
                    next_part, retry_at = 0, None
                else:
                    if pending["chat_id"] != str(env["TELEGRAM_CHAT_ID"]):
                        raise ValueError("Получатель недельного отчёта отличается от TELEGRAM_CHAT_ID")
                    text, chunks = pending["text"], json.loads(pending["chunks"])
                    next_part, retry_at = pending["next_part"], pending["retry_at"]
                if retry_at and common.now_utc() < common.parse_ts(retry_at):
                    return "Недельный отчёт в очереди: Telegram разрешит повтор после {}".format(retry_at)
                for index in range(next_part, len(chunks)):
                    try:
                        telegram.send(chunks[index], env["TELEGRAM_TOKEN"], env["TELEGRAM_CHAT_ID"])
                    except requests.RequestException as exc:
                        delay = telegram.retry_after(exc)
                        if delay is not None:
                            retry_at = (common.now_utc() + timedelta(seconds=delay)).isoformat()
                            with con:
                                con.execute("UPDATE weekly_delivery SET retry_at=? WHERE id=1", (retry_at,))
                        raise
                    with con:
                        con.execute("UPDATE weekly_delivery SET next_part=?, retry_at=NULL WHERE id=1",
                                    (index + 1,))
                with con:
                    con.execute("DELETE FROM weekly_delivery WHERE id=1")
                if not refresh:
                    break
                pending, refresh = None, False
            return text
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


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
    env = common.load_env()
    if args.send and not (env.get("TELEGRAM_TOKEN") and env.get("TELEGRAM_CHAT_ID")):
        print("Для --send нужны TELEGRAM_TOKEN и TELEGRAM_CHAT_ID.", file=sys.stderr)
        sys.exit(1)
    con = common.connect()

    errors = {}
    if not args.no_fetch:
        import fetch
        try:
            _n_new, errors = fetch.collect(cfg=cfg, con=con)
            for feed, err in errors.items():
                print("⚠️ {}: {}".format(feed, err), file=sys.stderr)
        except Exception as exc:                       # noqa: BLE001
            print("{} ({})".format(FETCH_ERROR_MSG, exc), file=sys.stderr)
            sys.exit(1)

    if args.mode == "pulse":
        if args.send:
            text = deliver_pulse(con, cfg, env)
        else:
            signals = pulse_signals(con, cfg)
            text = (format_pulse(signals) if signals else "Тихо: всплесков нет.")
            text += format_failed_feeds(con)
    elif args.mode == "weekly":
        text = (deliver_weekly(con, cfg, env) if args.send
                else build_weekly(con, cfg) + format_failed_feeds(con))
    else:
        path = build_map(con, cfg)
        text = "🗺 Карта трендов собрана: {}".format(path)

    print(text)
    if args.send and args.mode == "map":
        import telegram
        telegram.send(text, env["TELEGRAM_TOKEN"], env["TELEGRAM_CHAT_ID"])
    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
