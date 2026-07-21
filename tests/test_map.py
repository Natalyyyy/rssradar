import report


def test_render_map_html_is_selfcontained():
    clusters = [
        {"theme": "AI-агенты", "heat": 5,
         "items": [{"title": "Агенты в браузере", "link": "https://x/1"}]},
        {"theme": "Вайбкодинг", "heat": 2,
         "items": [{"title": "Скиллы", "link": "https://x/2"}]},
    ]
    html = report.render_map_html(clusters, "RSS-карта")
    assert "<html" in html.lower() and "</html>" in html.lower()
    assert "AI-агенты" in html and "https://x/1" in html
    assert "http://" not in html.split("https://x/1")[0][-200:]  # нет внешних CDN до контента


def test_render_map_html_empty():
    html = report.render_map_html([], "RSS-карта")
    assert "нет" in html.lower() or "empty" in html.lower()


def test_render_map_html_escapes_content():
    clusters = [
        {"theme": "<script>alert(1)</script>", "heat": 3,
         "items": [{"title": "T & <b>x</b>", "link": "https://x/1"}]},
    ]
    html = report.render_map_html(clusters, "RSS-карта")
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_claude_clusters_returns_none_on_bad_prompt_path():
    entries = [{"title": "t", "link": "https://x/1"}]
    result = report.claude_clusters(entries, "/nonexistent/prompt/path.md")
    assert result is None


def test_clean_clusters_drops_malformed():
    result = report._clean_clusters([
        {"theme": None, "heat": "n/a", "items": "nope"},
        "junk",
        {"theme": "X", "heat": 5, "items": [{"title": "t", "link": "https://x/1"}]},
    ])
    assert len(result) == 2
    assert result[0]["theme"] == ""
    assert result[0]["heat"] == 1
    assert result[0]["items"] == []
    assert result[1]["theme"] == "X"
    assert result[1]["heat"] == 5
    assert result[1]["items"] == [{"title": "t", "link": "https://x/1"}]

    html = report.render_map_html(result, "T")
    assert "https://x/1" in html


def test_clean_clusters_none_on_non_list():
    assert report._clean_clusters({"not": "a list"}) is None
    assert report._clean_clusters([]) is None


def test_render_map_html_blocks_unsafe_link_scheme():
    clusters = [
        {"theme": "T", "heat": 3,
         "items": [{"title": "Bad", "link": "javascript:alert(1)"}]},
    ]
    html = report.render_map_html(clusters, "T")
    assert "javascript:" not in html
    assert "href='javascript" not in html


def test_render_map_html_keeps_safe_https_link():
    clusters = [
        {"theme": "T", "heat": 3,
         "items": [{"title": "Good", "link": "https://x/1"}]},
    ]
    html = report.render_map_html(clusters, "T")
    assert "href='https://x/1'" in html
