"""Browser acceptance with synthetic data and intercepted network calls only."""

import json
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def conversion(identifier):
    return {"processing_id": identifier, "quota_remaining": 2, "quota_limit": 3, "quota_mode": "conversion",
            "analysis": {"analysis_id": identifier, "file_type": "pdf", "transactions_total": 1,
                         "total_inflows": 100, "total_outflows": 0, "net_total": 100,
                         "opening_balance": 0, "closing_balance": 100, "bank_code": "001",
                         "bank_branch": "0001", "account_number": "12345", "preview_transactions": [
                             {"date": "2026-10-01", "description": "Crédito fictício", "amount": 100,
                              "category": "Outros", "reconciliation_status": "unmatched"}], "insights": []}}


def check_converter(browser, width, count, filename):
    page = browser.new_page(viewport={"width": width, "height": 950})
    errors, answers, requests = [], {}, []
    scenario = {"fail_next": False}
    page.on("pageerror", lambda error: errors.append(str(error)))

    def route(request):
        path = urlparse(request.request.url).path
        requests.append(path)
        source = ROOT / "frontend" / path.lstrip("/")
        if source.is_file():
            mime = {".js": "application/javascript", ".css": "text/css", ".html": "text/html"}.get(source.suffix, "image/png")
            request.fulfill(body=source.read_bytes(), content_type=mime)
        elif path == "/auth/me":
            request.fulfill(status=401, json={})
        elif path == "/auth/session/refresh":
            request.fulfill(status=401, json={})
        elif path == "/auth/anonymous-session":
            request.fulfill(json={"identity_type": "anonymous"})
        elif path == "/api/conversion-runtime":
            request.fulfill(json={"direct_batch_enabled": True, "batch_max_files": count})
        elif path == "/api/conversion-batches":
            request.fulfill(json={"batch_id": "synthetic", "uploads": [{"url": "http://127.0.0.1:8099/upload-mock"}] * count})
        elif path == "/upload-mock" or path.endswith("/submit"):
            request.fulfill(json={})
        elif path == "/api/conversion-batches/synthetic":
            request.fulfill(json={"status": "completed", "jobs": [
                {"filename": f"arquivo-{index}.pdf", "status": "completed", "result": conversion(f"an_{index}")}
                for index in range(count)]})
        elif path.startswith("/conversion-feedback/"):
            identifier = path.rsplit("/", 1)[-1]
            answer = request.request.post_data_json["answer"]
            if scenario["fail_next"] and answer is not None:
                scenario["fail_next"] = False
                request.fulfill(status=503, json={})
            else:
                if answer is not None:
                    answers[identifier] = answer
                request.fulfill(json={"processing_id": identifier, "answer": answers.get(identifier)})
        elif path.startswith("/convert-edits/"):
            request.fulfill(json={"updated_at": "2026-10-29T15:00:00Z"})
        elif path.startswith("/convert-report/"):
            request.fulfill(body="OFXHEADER:100\n", content_type="application/x-ofx")
        elif path == "/banks":
            request.fulfill(json={"items": []})
        else:
            request.fulfill(status=404, body="")

    page.route("**/*", route)
    page.goto(f"http://127.0.0.1:8099/{filename}")
    if page.locator("#privacy-deny").is_visible():
        page.locator("#privacy-deny").click()
    page.wait_for_function("!document.getElementById('convert-btn').disabled || !document.getElementById('file-input').disabled")
    page.wait_for_function("document.getElementById('file-input').multiple === " + ("true" if count > 1 else "false"))
    page.locator("#file-input").set_input_files([
        {"name": f"arquivo-{index}.pdf", "mimeType": "application/pdf", "buffer": b"%PDF-1.4 synthetic"} for index in range(count)])
    page.locator("#convert-btn").click()
    survey = page.locator("#conversion-feedback")
    expect(survey).to_be_visible()
    yes_button = survey.get_by_role("button", name="Sim", exact=True)
    no_button = survey.get_by_role("button", name="Não", exact=True)
    yes_box, no_box = survey.evaluate("node => [...node.querySelectorAll('button')].map(button => button.getBoundingClientRect().toJSON())")
    assert 90 <= yes_box["width"] <= 120 and yes_box["height"] >= 42, "Survey buttons must be compact with a usable target"
    assert abs(yes_box["y"] - no_box["y"]) <= 1 and no_box["x"] > yes_box["x"], "Survey answers must sit side by side"
    if count == 1:
        expect(page.locator("#batch-results-panel")).to_be_hidden()
        expect(page.locator("#status-msg")).not_to_contain_text("Escolha")
    else:
        expect(page.locator("#batch-results-panel .batch-result-row")).to_have_count(count)
    # Downloads remain available before any answer and after a failed submission.
    download_button = page.locator("#download-ofx-btn, #download-report-btn").first
    expect(download_button).to_be_enabled()
    with page.expect_download():
        download_button.click()
    scenario["fail_next"] = True
    survey.get_by_role("button", name="Não", exact=True).click()
    expect(survey.get_by_role("status")).to_contain_text("Tente responder novamente")
    expect(download_button).to_be_enabled()
    survey.get_by_role("button", name="Sim", exact=True).click()
    expect(survey.get_by_role("status")).to_contain_text("Obrigado")
    expect(survey.get_by_role("button", name="Sim", exact=True)).to_have_attribute("aria-pressed", "true")
    expect(yes_button.locator(".feedback-check")).to_be_visible()
    expect(no_button.locator(".feedback-check")).to_be_hidden()
    if count > 1:
        page.locator("#batch-results-panel button").nth(1).click()
        expect(survey.get_by_role("button", name="Sim", exact=True)).to_have_attribute("aria-pressed", "false")
        survey.get_by_role("button", name="Não", exact=True).click()
        expect(survey.get_by_role("status")).to_contain_text("Obrigado")
        page.locator("#batch-results-panel button").first.click()
        expect(survey.get_by_role("button", name="Sim", exact=True)).to_have_attribute("aria-pressed", "true")
    page.reload()
    expect(survey.get_by_role("button", name="Sim", exact=True)).to_have_attribute("aria-pressed", "true")
    expect(page.locator("#menu-toggle")).to_be_visible() if width < 768 else expect(page.locator("#menu-toggle")).to_be_hidden()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "Horizontal overflow"
    output = ROOT / "tmp/ofx63-browser"
    output.mkdir(parents=True, exist_ok=True)
    survey.screenshot(path=str(output / f"survey-{width}-{count}-{filename}.png"))
    assert not errors, errors
    assert any(path.startswith("/conversion-feedback/") for path in requests)
    print(json.dumps({"page": filename, "width": width, "files": count, "answers": answers, "errors": errors}))
    page.close()


def check_admin(browser, width):
    page = browser.new_page(viewport={"width": width, "height": 950})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))

    def route(request):
        path = urlparse(request.request.url).path
        source = ROOT / "frontend" / path.lstrip("/")
        if source.is_file():
            mime = {".js": "application/javascript", ".css": "text/css", ".html": "text/html"}.get(source.suffix, "image/png")
            request.fulfill(body=source.read_bytes(), content_type=mime)
        elif path == "/admin/me":
            request.fulfill(json={"is_admin": True})
        elif path == "/admin/dashboard":
            request.fulfill(json={"conversion_feedback": {"yes_people": 7, "no_people": 2, "unanswered_people": 3,
                                                          "yes_count": 9, "no_count": 2, "unanswered_count": 4}})
        else:
            request.fulfill(status=404, body="")

    page.route("**/*", route)
    page.goto("http://127.0.0.1:8099/admin.html")
    feedback = page.locator("#dashboard-conversion-feedback")
    expect(feedback.locator(".metric-card")).to_have_count(3)
    expect(feedback).to_contain_text("Sem resposta")
    expect(feedback).to_contain_text("9 conversões")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "Admin overflow"
    feedback.screenshot(path=str(ROOT / f"tmp/ofx63-browser/admin-{width}.png"))
    page.locator("#admin-logout-btn").click()
    expect(feedback.locator(".metric-card")).to_have_count(0)
    assert not errors, errors
    print(json.dumps({"admin_width": width, "cards": 3, "errors": errors}))
    page.close()


def main():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            for width in (1440, 390):
                check_converter(browser, width, 1, "converter-pdf-para-ofx.html")
                check_converter(browser, width, 1, "converter-pdf-para-excel.html")
                check_converter(browser, width, 2, "converter-pdf-para-ofx.html")
                check_admin(browser, width)
        finally:
            browser.close()


if __name__ == "__main__":
    main()
