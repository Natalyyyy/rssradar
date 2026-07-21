import discover

HTML = '''
<html><head>
<link rel="alternate" type="application/rss+xml" href="/feed.xml">
<link rel="alternate" type="application/atom+xml" href="https://cdn.example.com/atom">
</head><body></body></html>
'''

def test_feed_links_absolute():
    links = discover.feed_links_in_html("https://example.com/blog", HTML)
    assert "https://example.com/feed.xml" in links
    assert "https://cdn.example.com/atom" in links

def test_common_paths_appended():
    links = discover.feed_links_in_html("https://example.com", "<html></html>")
    assert "https://example.com/feed" in links
    assert "https://example.com/rss" in links
