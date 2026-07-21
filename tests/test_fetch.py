from pathlib import Path
import feedparser
import common
import fetch

FIXTURE = Path(__file__).parent / "fixtures" / "sample_feed.xml"

def test_entries_from_parsed():
    parsed = feedparser.parse(FIXTURE.read_text(encoding="utf-8"))
    recs = fetch.entries_from_parsed("f1", parsed)
    assert len(recs) == 2
    assert recs[0]["entry_id"] == "a1"
    assert recs[0]["link"] == "https://example.com/a1"
    assert recs[0]["published"].startswith("2026-07-20")

def test_run_fetch_populates_db(monkeypatch):
    con = common.connect(":memory:")
    parsed = feedparser.parse(FIXTURE.read_text(encoding="utf-8"))
    monkeypatch.setattr(fetch, "_parse", lambda url, etag, modified: parsed)
    cfg = {"feeds": ["https://example.com/feed"]}
    n_new, errors = fetch.run_fetch(cfg=cfg, con=con)
    assert n_new == 2
    assert errors == {}
    assert con.execute("SELECT COUNT(*) FROM entries").fetchone()[0] == 2
    assert con.execute(
        "SELECT COUNT(*) FROM term_counts WHERE term='агент'").fetchone()[0] >= 1
