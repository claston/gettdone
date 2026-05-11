from xml.etree import ElementTree

from fastapi.testclient import TestClient

from app.main import app


def test_robots_txt_points_crawlers_to_sitemap() -> None:
    client = TestClient(app)

    response = client.get("/robots.txt")

    assert response.status_code == 200
    lines = set(response.text.splitlines())
    expected_lines = {
        "User-agent: *",
        "Disallow: /admin.html",
        "Sitemap: https://www.ofxsimples.com.br/sitemap.xml",
    }

    assert expected_lines.issubset(lines)


def test_sitemap_xml_lists_public_indexable_pages() -> None:
    client = TestClient(app)

    response = client.get("/sitemap.xml")

    assert response.status_code == 200
    root = ElementTree.fromstring(response.text)
    namespace = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    urls = {loc.text for loc in root.findall("sm:url/sm:loc", namespace)}
    expected_urls = {
        "https://www.ofxsimples.com.br/",
        "https://www.ofxsimples.com.br/convert.html",
        "https://www.ofxsimples.com.br/converter-pdf-para-ofx.html",
        "https://www.ofxsimples.com.br/blog/o-que-e-ofx-e-como-usar/",
    }
    blocked_urls = {"https://www.ofxsimples.com.br/ofx-convert.html"}

    assert expected_urls.difference(urls) == set()
    assert blocked_urls.isdisjoint(urls)
