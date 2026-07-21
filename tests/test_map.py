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
