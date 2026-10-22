"""Общие утилиты rssradar: .env, конфиг, база, нормализация терминов."""
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

class ConfigError(Exception):
    pass


ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "rssradar.db"
CONFIG_PATH = ROOT / "config.json"
ENV_PATH = ROOT / ".env"

DEFAULTS = {
    "feeds": [],
    "pulse_min_feeds": 2,
    "pulse_spike_multiplier": 2.0,
    "pulse_baseline_days": 14,
    "pulse_window_days": 2,
    "pulse_cooldown_days": 5,
    "weekly_window_days": 7,
    "fetch_limit_cold": 30,
    "weekly_day": "sun",
    "weekly_time": "18:00",
    "pulse_time": "10:00",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    feed_url   TEXT NOT NULL,
    entry_id   TEXT NOT NULL,
    title      TEXT,
    link       TEXT,
    published  TEXT,
    summary    TEXT,
    first_seen TEXT NOT NULL,
    PRIMARY KEY (feed_url, entry_id)
);
CREATE TABLE IF NOT EXISTS term_counts (
    term     TEXT NOT NULL,
    day      TEXT NOT NULL,
    feed_url TEXT NOT NULL,
    PRIMARY KEY (term, day, feed_url)
);
CREATE TABLE IF NOT EXISTS pulse_queue (
    id         INTEGER PRIMARY KEY CHECK (id = 1),
    text       TEXT NOT NULL,
    terms      TEXT NOT NULL,
    chat_id    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pulse_delivery (
    id         INTEGER PRIMARY KEY CHECK (id = 1),
    chunks     TEXT NOT NULL,
    next_part  INTEGER NOT NULL DEFAULT 0,
    retry_at   TEXT
);
CREATE TABLE IF NOT EXISTS weekly_delivery (
    created_at TEXT,
    id         INTEGER PRIMARY KEY CHECK (id = 1),
    text       TEXT NOT NULL,
    chat_id    TEXT NOT NULL,
    chunks     TEXT NOT NULL,
    next_part  INTEGER NOT NULL DEFAULT 0,
    retry_at   TEXT
);
CREATE TABLE IF NOT EXISTS pulse_alerts (
    term       TEXT PRIMARY KEY,
    alerted_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS feed_status (
    feed_url  TEXT PRIMARY KEY,
    etag      TEXT,
    modified  TEXT,
    last_ok   TEXT,
    last_error TEXT
);
"""


def load_env(path=None):
    path = Path(path or ENV_PATH)
    env = {}
    if not path.exists():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip().strip('"').strip("'")
        if value:
            env[key.strip()] = value
    return env


def load_config(path=None):
    path = Path(path or CONFIG_PATH)
    cfg = dict(DEFAULTS)
    if path.exists():
        try:
            cfg.update(json.loads(path.read_text(encoding="utf-8")))
        except json.JSONDecodeError as exc:
            raise ConfigError(
                "❌ {} повреждён — проверь JSON (лишняя запятая?): {}".format(path, exc))
    return cfg


def now_utc():
    return datetime.now(timezone.utc)


def parse_ts(value):
    """ISO-таймстамп → aware datetime. Терпит 'Z' и '+0000' (Python 3.9)."""
    value = value.replace("Z", "+00:00")
    if value.endswith("+0000"):
        value = value[:-5] + "+00:00"
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def day_of(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d")


def connect(db_path=None):
    path = Path(db_path or DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    if SCHEMA.strip():
        con.executescript(SCHEMA)
        columns = {row["name"] for row in con.execute("PRAGMA table_info(weekly_delivery)")}
        if columns and "created_at" not in columns:
            con.execute("ALTER TABLE weekly_delivery ADD COLUMN created_at TEXT")
            con.commit()
    return con


def save_entries(con, records, commit=True):
    new = 0
    stamp = now_utc().isoformat()
    for r in records:
        cur = con.execute(
            "INSERT OR IGNORE INTO entries "
            "(feed_url, entry_id, title, link, published, summary, first_seen) "
            "VALUES (?,?,?,?,?,?,?)",
            (r["feed_url"], r["entry_id"], r.get("title", ""), r.get("link", ""),
             r.get("published", ""), r.get("summary", ""), stamp),
        )
        new += cur.rowcount
    if commit:
        con.commit()
    return new


def record_terms(con, records, commit=True):
    for r in records:
        published = r.get("published") or ""
        day = None
        if published:
            try:
                day = day_of(parse_ts(published))
            except ValueError:
                day = None
        if day is None:
            row = con.execute(
                "SELECT first_seen FROM entries WHERE feed_url=? AND entry_id=?",
                (r["feed_url"], r["entry_id"]),
            ).fetchone()
            if row and row["first_seen"]:
                try:
                    day = day_of(parse_ts(row["first_seen"]))
                except ValueError:
                    day = None
        if day is None:
            day = day_of(now_utc())
        if day > day_of(now_utc()):
            continue
        for term in set(normalize_terms(r.get("title", ""))):
            con.execute(
                "INSERT OR IGNORE INTO term_counts (term, day, feed_url) VALUES (?,?,?)",
                (term, day, r["feed_url"]),
            )
    if commit:
        con.commit()


def get_feed_cache(con, feed_url):
    row = con.execute(
        "SELECT etag, modified FROM feed_status WHERE feed_url = ?", (feed_url,)
    ).fetchone()
    return (row["etag"], row["modified"]) if row else (None, None)


def set_feed_status(con, feed_url, etag=None, modified=None, error=None, commit=True):
    stamp = now_utc().isoformat()
    if error is None:
        con.execute(
            "INSERT INTO feed_status (feed_url, etag, modified, last_ok, last_error) "
            "VALUES (?,?,?,?,NULL) "
            "ON CONFLICT(feed_url) DO UPDATE SET etag=excluded.etag, "
            "modified=excluded.modified, last_ok=excluded.last_ok, last_error=NULL",
            (feed_url, etag, modified, stamp),
        )
    else:
        con.execute(
            "INSERT INTO feed_status (feed_url, last_error) VALUES (?,?) "
            "ON CONFLICT(feed_url) DO UPDATE SET last_error=excluded.last_error",
            (feed_url, error),
        )
    if commit:
        con.commit()


def failed_feeds(con):
    """Фиды, последний прогон fetch которых закончился ошибкой (last_error IS NOT NULL).
    После успешного повторного фетча set_feed_status чистит last_error → список актуальный."""
    rows = con.execute(
        "SELECT feed_url, last_error FROM feed_status WHERE last_error IS NOT NULL"
    ).fetchall()
    return [(r["feed_url"], r["last_error"]) for r in rows]


def data_span_days(con, now=None):
    row = con.execute("SELECT MIN(day) AS lo, MAX(day) AS hi FROM term_counts WHERE day <= ?",
                      (day_of(now or now_utc()),)).fetchone()
    if not row or not row["lo"]:
        return 0
    lo = parse_ts(row["lo"] + "T00:00:00Z")
    hi = parse_ts(row["hi"] + "T00:00:00Z")
    return int((hi - lo).total_seconds() // 86400)


def was_alerted_recently(con, term, now, cooldown_days):
    row = con.execute(
        "SELECT alerted_at FROM pulse_alerts WHERE term = ?", (term,)
    ).fetchone()
    if not row:
        return False
    return parse_ts(row["alerted_at"]) >= now - timedelta(days=cooldown_days)


def mark_alerted(con, terms, at=None, commit=True):
    stamp = (at or now_utc()).isoformat()
    con.executemany(
        "INSERT INTO pulse_alerts (term, alerted_at) VALUES (?,?) "
        "ON CONFLICT(term) DO UPDATE SET alerted_at=excluded.alerted_at",
        [(t, stamp) for t in terms],
    )
    if commit:
        con.commit()


import re

try:
    import pymorphy3
    _MORPH = pymorphy3.MorphAnalyzer()
except Exception:          # noqa: BLE001 — нет pymorphy3 → фолбэк без лемм
    _MORPH = None

_TOKEN_RE = re.compile(r"[а-яёa-z0-9]+", re.IGNORECASE)
_RU_STOP = {
    "и", "в", "во", "не", "что", "он", "на", "я", "с", "со", "как", "а", "то",
    "все", "она", "так", "его", "но", "да", "ты", "к", "у", "же", "вы", "за",
    "бы", "по", "только", "ее", "мне", "было", "вот", "от", "меня", "о", "из",
    "для", "про", "это", "этот", "эта", "мы", "они", "или", "при", "над", "под",
}
_EN_STOP = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "is", "are",
    "was", "with", "as", "at", "by", "from", "how", "why", "what", "new", "your",
}


def normalize_terms(text):
    terms = []
    for tok in _TOKEN_RE.findall((text or "").lower()):
        if len(tok) < 3:
            continue
        if re.search(r"[а-яё]", tok):
            if tok in _RU_STOP:
                continue
            lemma = _MORPH.parse(tok)[0].normal_form if _MORPH else tok
        else:
            if tok in _EN_STOP:
                continue
            lemma = tok
        terms.append(lemma)
    return terms
