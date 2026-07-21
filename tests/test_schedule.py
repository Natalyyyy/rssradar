import common
import schedule


def test_cron_lines_have_both_jobs():
    cfg = common.DEFAULTS.copy()
    lines = schedule.cron_lines(cfg)
    assert any("pulse" in l for l in lines)
    assert any("weekly" in l for l in lines)
    assert all(schedule.CRON_MARK in l for l in lines)
