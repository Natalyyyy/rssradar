import common


def make_con():
    con = common.connect(":memory:")
    return con


def rec(feed, eid, title, published):
    return {"feed_url": feed, "entry_id": eid, "title": title,
            "link": "https://x/" + eid, "published": published, "summary": ""}


def test_save_entries_dedup():
    con = make_con()
    r = rec("f1", "e1", "AI агенты", "2026-07-20T09:00:00Z")
    assert common.save_entries(con, [r]) == 1
    assert common.save_entries(con, [r]) == 0        # повтор не считается


def test_record_terms_unique_triple():
    con = make_con()
    common.record_terms(con, [rec("f1", "e1", "агенты агенты", "2026-07-20T09:00:00Z")])
    common.record_terms(con, [rec("f1", "e2", "агенты снова", "2026-07-20T12:00:00Z")])
    n = con.execute(
        "SELECT COUNT(*) FROM term_counts WHERE term='агент'").fetchone()[0]
    assert n == 1                                    # тот же term+day+feed → одна строка


def test_data_span_days():
    con = make_con()
    common.record_terms(con, [rec("f1", "e1", "тема", "2026-07-10T09:00:00Z")])
    common.record_terms(con, [rec("f1", "e2", "тема", "2026-07-20T09:00:00Z")])
    assert common.data_span_days(con) == 10


def test_alert_cooldown():
    con = make_con()
    now = common.now_utc()
    assert common.was_alerted_recently(con, "агент", now, 5) is False
    common.mark_alerted(con, ["агент"], at=now)
    assert common.was_alerted_recently(con, "агент", now, 5) is True
