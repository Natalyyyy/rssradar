from datetime import timedelta
import common
import report

def seed(con, term_days_feeds):
    for term, day, feed in term_days_feeds:
        con.execute("INSERT OR IGNORE INTO term_counts (term, day, feed_url) VALUES (?,?,?)",
                    (term, day, feed))
    con.commit()

def test_spike_detected_with_history():
    con = common.connect(":memory:")
    now = common.parse_ts("2026-07-21T10:00:00Z")
    days = [(now - timedelta(days=d)).strftime("%Y-%m-%d") for d in range(0, 20)]
    # старая история "агент": по 1 упоминанию в день, 1 фид (низкая база)
    seed(con, [("агент", days[d], "f1") for d in range(3, 18)])
    # окно: всплеск — 4 фида за 2 дня (recent_daily=2.5 против baseline_daily=1.0 → ratio=2.5)
    seed(con, [("агент", days[0], "f1"), ("агент", days[0], "f2"), ("агент", days[0], "f4"),
               ("агент", days[1], "f3"), ("агент", days[1], "f4")])
    cfg = common.DEFAULTS.copy()
    sig = report.pulse_signals(con, cfg, now=now)
    assert any(s["term"] == "агент" and s["recent_feeds"] >= 2 for s in sig)

def test_silence_without_baseline():
    con = common.connect(":memory:")
    now = common.parse_ts("2026-07-21T10:00:00Z")
    d = now.strftime("%Y-%m-%d")
    seed(con, [("агент", d, "f1"), ("агент", d, "f2"), ("агент", d, "f3")])
    cfg = common.DEFAULTS.copy()
    assert report.pulse_signals(con, cfg, now=now) == []   # истории нет

def test_cooldown_suppresses():
    con = common.connect(":memory:")
    now = common.parse_ts("2026-07-21T10:00:00Z")
    days = [(now - timedelta(days=d)).strftime("%Y-%m-%d") for d in range(0, 20)]
    seed(con, [("агент", days[d], "f1") for d in range(3, 18)])
    seed(con, [("агент", days[0], "f1"), ("агент", days[0], "f2"),
               ("агент", days[1], "f3")])
    common.mark_alerted(con, ["агент"], at=now)
    cfg = common.DEFAULTS.copy()
    assert all(s["term"] != "агент" for s in report.pulse_signals(con, cfg, now=now))
