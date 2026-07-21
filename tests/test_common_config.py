import json
from datetime import timezone
import common

import pytest

def test_load_config_merges_defaults(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"feeds": ["https://a/feed"], "pulse_min_feeds": 3}))
    cfg = common.load_config(p)
    assert cfg["feeds"] == ["https://a/feed"]
    assert cfg["pulse_min_feeds"] == 3           # override
    assert cfg["weekly_day"] == "sun"            # default preserved

def test_load_env_ignores_comments(tmp_path):
    p = tmp_path / ".env"
    p.write_text('# comment\nTELEGRAM_TOKEN="abc"\nEMPTY=\n')
    env = common.load_env(p)
    assert env["TELEGRAM_TOKEN"] == "abc"
    assert "EMPTY" not in env

def test_parse_ts_and_day_of():
    dt = common.parse_ts("2026-07-21T10:00:00Z")
    assert dt.tzinfo == timezone.utc
    assert common.day_of(dt) == "2026-07-21"

def test_load_config_raises_friendly_error_on_malformed_json(tmp_path):
    p = tmp_path / "config.json"
    p.write_text('{"feeds": [,]}')
    with pytest.raises(common.ConfigError) as exc_info:
        common.load_config(p)
    assert "повреждён" in str(exc_info.value)
