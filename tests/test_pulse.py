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
    # старая история "агент": по 1 упоминанию в день, 1 фид (низкая база).
    # база — ровно pulse_baseline_days=14 дней перед окном: distance 2..15
    seed(con, [("агент", days[d], "f1") for d in range(2, 16)])
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
    # тот же настоящий всплеск, что и в test_spike_detected_with_history (ratio=2.5 ≥ 2.0),
    # чтобы кулдаун был единственной причиной подавления, а не сам порог всплеска
    seed(con, [("агент", days[d], "f1") for d in range(2, 16)])
    seed(con, [("агент", days[0], "f1"), ("агент", days[0], "f2"), ("агент", days[0], "f4"),
               ("агент", days[1], "f3"), ("агент", days[1], "f4")])
    common.mark_alerted(con, ["агент"], at=now)
    cfg = common.DEFAULTS.copy()
    assert all(s["term"] != "агент" for s in report.pulse_signals(con, cfg, now=now))

def test_steady_traffic_no_spike():
    # ровный трафик: термин раз в день в ДВУХ фидах на всём пути база+окно (distance 0..17,
    # с запасом сверх минимально нужных 16 дней = win_days(2) + base_days(14))
    con = common.connect(":memory:")
    now = common.parse_ts("2026-07-21T10:00:00Z")
    days = [(now - timedelta(days=d)).strftime("%Y-%m-%d") for d in range(0, 18)]
    seed(con, [("термин", days[d], feed) for d in range(0, 18) for feed in ("f1", "f2")])
    cfg = common.DEFAULTS.copy()
    # Порог занижен нарочно (1.3), чтобы сделать баг наблюдаемым: при верном окне
    # ratio≈1.0 (< 1.3 → сигнала нет), а при старом off-by-one окне (win_days+1 дней
    # в числителе, но деление на win_days) ratio≈1.5 (>= 1.3 → ложный сигнал).
    # На проде порог 2.0, и оба значения (1.0 и 1.5) под ним — баг маскируется там,
    # поэтому тест обнажает его через заниженный порог.
    cfg["pulse_spike_multiplier"] = 1.3
    sig = report.pulse_signals(con, cfg, now=now)
    assert all(s["term"] != "термин" for s in sig)
