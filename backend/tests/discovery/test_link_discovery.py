"""M-09 Task 7: site expansion — sitemap / RSS / Atom / HTML nav / pagination / internal."""

from __future__ import annotations

from app.discovery.link_discovery import extract_html_links, parse_feed, parse_sitemap

SITEMAP = (
    '<?xml version="1.0"?>'
    '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
    "<url><loc>https://example.com/a</loc></url>"
    "<url><loc>https://example.com/b?x=1</loc></url>"
    "</urlset>"
)

SITEMAP_INDEX = (
    '<?xml version="1.0"?>'
    '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
    "<sitemap><loc>https://example.com/sitemap-1.xml</loc></sitemap>"
    "</sitemapindex>"
)

RSS = (
    '<rss version="2.0"><channel>'
    "<item><link>https://example.com/p1</link></item>"
    "<item><link>https://example.com/p2</link></item>"
    "</channel></rss>"
)

ATOM = (
    '<feed xmlns="http://www.w3.org/2005/Atom">'
    '<entry><link href="https://example.com/p3"/></entry>'
    "</feed>"
)

HTML = (
    "<html><body>"
    '<nav><a href="/products">Products</a></nav>'
    '<a rel="next" href="/list?page=2">Next</a>'
    '<a href="/detail/1">item</a>'
    '<a href="https://other.com/x">external</a>'
    '<img src="https://example.com/logo.png">'
    "</body></html>"
)


def test_parse_sitemap_and_index() -> None:
    assert parse_sitemap(SITEMAP) == ["https://example.com/a", "https://example.com/b?x=1"]
    assert parse_sitemap(SITEMAP_INDEX) == ["https://example.com/sitemap-1.xml"]


def test_parse_rss_and_atom() -> None:
    assert parse_feed(RSS) == ["https://example.com/p1", "https://example.com/p2"]
    assert parse_feed(ATOM) == ["https://example.com/p3"]


def test_extract_html_links_includes_nav_pagination_internal_excludes_asset() -> None:
    links = extract_html_links(HTML, "https://example.com/list")
    hrefs = {h for h, _ in links}
    assert "https://example.com/products" in hrefs
    assert "https://example.com/list?page=2" in hrefs  # rel=next + page=N → pagination
    assert "https://example.com/detail/1" in hrefs
    assert "https://other.com/x" in hrefs  # 跨域保留为候选提示
    assert "https://example.com/logo.png" not in hrefs  # 资产链接排除
    pagination = [h for h, rel in links if rel == "pagination"]
    assert "https://example.com/list?page=2" in pagination
