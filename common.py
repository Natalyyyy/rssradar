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
