import pytest
import common
import report


@pytest.mark.parametrize("links,count", [
    (["https://x/?p=1", "https://x/?p=2"], 2),
    (["https://x/Post", "https://x/post"], 2),
    (["https://x/Post?id=1&utm_source=a", "https://x/Post?id=1&utm_source=b"], 1),
    (["https://X/Post", "https://x/Post"], 1),
    (["https://x/?tag=A&tag=B", "https://x/?tag=A"], 2),
    (["", ""], 2),
    (["https://x/?utm_custom=1", "https://x/?utm_custom=2"], 2),
])
def test_weekly_dedup_preserves_article_identity(links, count):
    con = common.connect(":memory:")
    common.save_entries(con, [{"feed_url":"f", "entry_id":str(i), "link":link}
                              for i, link in enumerate(links)])
    for _ in range(2):
        assert len(report.weekly_entries(con, common.DEFAULTS)) == count


def test_empty_link():
    assert report._norm_link(None) == ""


def test_malformed_link_does_not_crash_report():
    con = common.connect(":memory:")
    common.save_entries(con, [{"feed_url":"f", "entry_id":"1", "link":"https://[broken"}])
    assert len(report.weekly_entries(con, common.DEFAULTS)) == 1
