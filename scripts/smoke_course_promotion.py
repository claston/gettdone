"""Affiliate UX acceptance with synthetic conversions; no requests reach Hotmart."""

import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import expect, sync_playwright

from smoke_conversion_feedback import conversion

ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8099"
OUTPUT = ROOT / "tmp/course-promotion-preview"


def check(browser, filename, width, identity):
    context = browser.new_context(viewport={"width": width, "height": 960})
    events, feedback, errors = [], [], []
    scenario = {"telemetry_down": False}
    user = {"email": "synthetic@example.invalid", "name": "Synthetic", "plan_code": "free",
            "quota_mode": "conversion", "quota_remaining": 9, "quota_limit": 10}
    if identity == "paid":
        user.update(plan_code="essencial", quota_mode="pages")

    def route(request):
        parsed = urlparse(request.request.url)
        path = parsed.path
        source = ROOT / "frontend" / path.lstrip("/")
        if parsed.hostname == "go.hotmart.com":
            request.fulfill(body="Synthetic affiliate destination", content_type="text/html")
        elif source.is_file():
            mime = {".js": "application/javascript", ".css": "text/css", ".html": "text/html"}.get(source.suffix, "image/png")
            request.fulfill(body=source.read_bytes(), content_type=mime)
        elif path == "/auth/me":
            request.fulfill(status=401 if identity == "anonymous" else 200, json={} if identity == "anonymous" else user)
        elif path == "/auth/session/refresh":
            request.fulfill(status=401, json={})
        elif path == "/auth/anonymous-session":
            request.fulfill(json={"identity_type": "anonymous"})
        elif path == "/api/conversion-runtime":
            request.fulfill(json={"direct_batch_enabled": True, "batch_max_files": 1})
        elif path == "/api/conversion-batches":
            request.fulfill(json={"batch_id": "synthetic", "uploads": [{"url": BASE + "/upload-mock"}]})
        elif path == "/upload-mock" or path.endswith("/submit"):
            request.fulfill(json={})
        elif path == "/api/conversion-batches/synthetic":
            result = conversion("an_course")
            result["quota_mode"] = user["quota_mode"]
            request.fulfill(json={"status": "completed", "jobs": [
                {"filename": "synthetic.pdf", "status": "completed", "result": result}]})
        elif path == "/telemetry/events":
            events.append(request.request.post_data_json)
            request.fulfill(status=503 if scenario["telemetry_down"] else 202, body="")
        elif path.startswith("/conversion-feedback/"):
            feedback.append(request.request.post_data_json)
            request.fulfill(json={"processing_id": "an_course", "answer": None})
        elif path.startswith("/convert-edits/"):
            request.fulfill(json={"updated_at": "2026-11-17T15:00:00Z"})
        elif path.startswith("/convert-report/"):
            request.fulfill(body="OFXHEADER:100\n", content_type="application/x-ofx")
        elif path == "/banks":
            request.fulfill(json={"items": []})
        else:
            request.fulfill(status=404, body="")

    context.route("**/*", route)
    page = context.new_page()
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(f"{BASE}/{filename}")
    if page.locator("#privacy-deny").is_visible():
        page.locator("#privacy-deny").click()
    expect(page.locator("#course-promotion")).to_have_count(0)
    # Keep the prompt off screen until an explicit scroll: DOM insertion is not an impression.
    page.add_style_tag(content="#course-promotion { margin-top: 1800px !important; }")
    page.locator("#file-input").set_input_files({"name": "synthetic.pdf", "mimeType": "application/pdf", "buffer": b"%PDF-1.4 synthetic"})
    page.locator("#convert-btn").click()
    download = page.locator("#download-ofx-btn, #download-report-btn").first
    expect(download).to_be_enabled()
    if identity == "paid":
        expect(page.locator("#conversion-feedback")).to_be_visible()
        expect(page.locator("#course-promotion")).to_have_count(0)
        assert not events
    else:
        card = page.locator("#course-promotion")
        expect(card).to_be_visible()
        expect(card).to_contain_text("Parceria")
        expect(page.locator("#conversion-feedback")).to_be_hidden()
        assert not any(event["event_type"] == "course_cta_view" for event in events)
        with page.expect_request(lambda request: request.url.endswith("/telemetry/events")
                                 and request.post_data_json.get("event_type") == "course_cta_view"):
            card.scroll_into_view_if_needed()
        with page.expect_download():
            download.click()
        page.reload()
        expect(card).to_be_visible()
        page.wait_for_function("document.getElementById('download-section').classList.contains('hidden') === false")
        assert sum(e["event_type"] == "course_cta_view" for e in events) == 1, events
        assert not feedback, "Hidden survey must not register an unanswered impression"
        with page.expect_popup() as opened:
            card.get_by_role("link", name="Saiba como").click()
        landing = opened.value
        landing.on("pageerror", lambda error: errors.append(str(error)))
        expect(landing.get_by_role("heading", level=1)).to_contain_text("Aprenda a elaborar")
        offer = landing.locator("#course-offer-link")
        expect(offer).to_have_attribute("href", "https://go.hotmart.com/D87342786L?src=ofxs%7Cconversao%7Cdcne")
        expect(offer).to_have_attribute("rel", "sponsored noopener noreferrer")
        assert landing.evaluate("document.documentElement.scrollWidth <= innerWidth")
        scenario["telemetry_down"] = True
        with landing.expect_popup() as destination:
            offer.click()
        destination.value.wait_for_load_state()
        assert parse_qs(urlparse(destination.value.url).query)["src"] == ["ofxs|conversao|dcne"]
        destination.value.close()
        assert {e["event_type"] for e in events} == {
            "course_cta_view", "course_cta_click", "course_page_view", "course_outbound_click"}, events
        if identity == "anonymous" and filename == "converter-pdf-para-ofx.html":
            OUTPUT.mkdir(parents=True, exist_ok=True)
            landing.screenshot(path=str(OUTPUT / f"course-{width}.png"), full_page=True)
            card.screenshot(path=str(OUTPUT / f"cta-{width}.png"))
        with page.expect_download():
            download.click()
        page.locator("#clear-file-btn").click()
        expect(card).to_be_hidden()
        landing.close()
    assert not errors, errors
    print(json.dumps({"page": filename, "width": width, "identity": identity, "passed": True}))
    context.close()


def check_sources(browser):
    context = browser.new_context()
    page = context.new_page()

    def route(request):
        source = ROOT / "frontend" / urlparse(request.request.url).path.lstrip("/")
        if source.is_file():
            mime = {".js": "application/javascript", ".css": "text/css", ".html": "text/html"}.get(source.suffix, "image/png")
            request.fulfill(body=source.read_bytes(), content_type=mime)
        else:
            request.fulfill(status=503, body="")

    context.route("**/*", route)
    for query, expected in (("", "pagina"), ("?from=email", "email"), ("?from=private@example.invalid&src=bad", "pagina")):
        page.goto(BASE + "/curso-notas-explicativas.html" + query)
        expect(page.locator("#course-offer-link")).to_have_attribute(
            "href", f"https://go.hotmart.com/D87342786L?src=ofxs%7C{expected}%7Cdcne")
    context.close()
    context = browser.new_context(java_script_enabled=False)
    context.route("**/*", route)
    page = context.new_page()
    page.goto(BASE + "/curso-notas-explicativas.html")
    expect(page.locator("#course-offer-link")).to_have_attribute(
        "href", "https://go.hotmart.com/D87342786L?src=ofxs%7Cpagina%7Cdcne")
    context.close()
    print("Sources allowlist, telemetry outage and no-JavaScript fallback passed")


if __name__ == "__main__":
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        for width in (390, 1440):
            for filename in ("ofx-convert.html", "converter-pdf-para-ofx.html", "converter-pdf-para-excel.html"):
                for identity in ("anonymous", "free", "paid"):
                    check(browser, filename, width, identity)
        check_sources(browser)
        browser.close()
