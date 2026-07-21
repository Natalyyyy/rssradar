"""Общие утилиты rssradar: .env, конфиг, база, нормализация терминов."""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

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

SCHEMA = ""  # наполняется в Task 3


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
        cfg.update(json.loads(path.read_text(encoding="utf-8")))
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
    return con


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
