"""Responsive course funnel and identity badges, with intercepted synthetic data."""
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "tmp/course-promotion-preview"


def check(browser, width):
    page = browser.new_page(viewport={"width": width, "height": 950})
    errors, queries = [], []
    page.on("pageerror", lambda error: errors.append(str(error)))
    buyer = {"display_name": "Pessoa de exemplo", "email": "synthetic@example.invalid", "identity_type": "registered",
             "identity_reference": "synthetic", "course_page_views": 3, "course_outbound_clicks": 1,
             "last_course_page_view_at": "2026-11-17T15:01:00+00:00", "last_course_outbound_click_at": "2026-11-17T15:02:00+00:00",
             "purchase_profile": "acompanhar", "marketing_contact_allowed": False, "plan_code": "free", "conversions": 0}

    def route(request):
        parsed = urlparse(request.request.url)
        source = ROOT / "frontend" / parsed.path.lstrip("/")
        if source.is_file():
            mime = {".js": "application/javascript", ".css": "text/css", ".html": "text/html"}.get(source.suffix, "image/png")
            request.fulfill(body=source.read_bytes(), content_type=mime)
        elif parsed.path == "/admin/me":
            request.fulfill(json={"is_admin": True})
        elif parsed.path == "/admin/dashboard/overview":
            request.fulfill(json={"summary": {}, "checkout_funnel": {}, "commercial_interest": [], "course_funnel": [
                {"source": "conversao", "cta_view_people": 100, "cta_click_people": 20, "page_view_people": 18,
                 "outbound_click_people": 9, "cta_ctr": 20, "page_ctr": 50},
                {"source": "pagina", "page_view_people": 10, "outbound_click_people": 1, "page_ctr": 10},
                {"source": "email", "page_view_people": 0, "outbound_click_people": 0, "page_ctr": None},
            ]})
        elif parsed.path == "/admin/active-users":
            query = parse_qs(parsed.query)
            queries.append(query)
            request.fulfill(json={"items": [buyer], "total": 1, "summary": {"active_people": 1, "registered_people": 1}})
        elif parsed.path == "/admin/auth/logout":
            request.fulfill(json={})
        else:
            request.fulfill(status=404, body="")

    page.route("**/*", route)
    page.goto("http://127.0.0.1:8099/admin.html")
    if page.locator("#privacy-deny").is_visible():
        page.locator("#privacy-deny").click()
    funnel = page.locator("#dashboard-course-funnel")
    expect(funnel.locator(".metric-card")).to_have_count(6)
    expect(funnel).to_contain_text("20 / 100 pessoas")
    expect(funnel).to_contain_text("9 / 18 visitantes")
    page.locator("#dashboard-course-source").select_option("pagina")
    expect(funnel.locator(".metric-card")).to_have_count(3)
    expect(funnel).to_contain_text("1 / 10 visitantes")
    page.locator("#dashboard-course-source").select_option("email")
    expect(funnel).to_contain_text("—")
    page.locator("#dashboard-course-source").select_option("conversao")
    page.locator("#dashboard-period").select_option("1")
    expect(funnel.locator(".metric-card")).to_have_count(6)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    page.locator("section[aria-labelledby='dashboard-course-title']").screenshot(path=str(OUTPUT / f"admin-funnel-{width}.png"))
    page.locator("#dashboard-course-visitors-btn").click()
    expect(page.locator("#active-users-course-interest")).to_have_value("visited")
    expect(page.locator("#active-users-period")).to_have_value("1")
    card = page.locator("#active-users-list .order-card").first
    expect(card.locator(".badge", has_text="Visitou o curso")).to_be_visible()
    expect(card.locator(".badge", has_text="Clicou para ver a oferta")).to_be_visible()
    expect(card).to_contain_text("Sem consentimento de marketing")
    expect(card).to_contain_text("Última visita ao curso")
    assert queries[-1]["course_interest"] == ["visited"] and queries[-1]["days"] == ["1"]
    with page.expect_request(lambda request: "course_interest=outbound" in request.url):
        page.locator("#active-users-course-interest").select_option("outbound")
    expect(card).to_contain_text("Visitas ao curso")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "Horizontal overflow"
    card.screenshot(path=str(OUTPUT / f"admin-course-visitor-{width}.png"))
    page.locator("#admin-logout-btn").click()
    expect(funnel.locator(".metric-card")).to_have_count(0)
    expect(page.locator("#active-users-list .order-card")).to_have_count(0)
    assert not errors, errors
    page.close()
    print(json.dumps({"width": width, "course_funnel_badges_filters_logout": "passed"}))


if __name__ == "__main__":
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            for width in (390, 1440):
                check(browser, width)
        finally:
            browser.close()
