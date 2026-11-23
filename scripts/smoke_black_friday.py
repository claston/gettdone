"""Check promotional pricing, checkout selection and responsiveness with synthetic data."""
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
PLANS = [
    dict(code=code, name=name, price_cents=price, quota_limit=quota, billing_period=period,
         max_upload_size_bytes=10485760, currency="BRL")
    for code, name, price, quota, period in (
        ("essencial_black", "Essencial Black", 1990, 600, "monthly"),
        ("profissional_black", "Profissional Black", 2990, 1000, "monthly"),
        ("black_friday_3_meses", "Black Friday 3 meses", 5990, 1000, "prepaid_quarter"),
    )
]


def run():
    output = ROOT / "tmp/black-friday"
    output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            for viewport, width in (("desktop", 1440), ("mobile", 390)):
                page = browser.new_page(viewport={"width": width, "height": 1000})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                state = {"plans": PLANS}

                def route(request):
                    path = urlparse(request.request.url).path
                    if path == "/plans":
                        request.fulfill(json={"items": state["plans"]})
                    elif path == "/public-config":
                        request.fulfill(json={})
                    elif path in ("/auth/me", "/auth/session/refresh"):
                        request.fulfill(status=401, json={})
                    elif path == "/telemetry/events" or path == "/auth/anonymous-session":
                        request.fulfill(json={"ok": True})
                    elif Path(path).suffix in (".html", ".js", ".css") and (ROOT / "frontend" / path.lstrip("/")).is_file():
                        request.fulfill(body=(ROOT / "frontend" / path.lstrip("/")).read_bytes(),
                                        content_type={".html": "text/html", ".js": "application/javascript",
                                                      ".css": "text/css"}[Path(path).suffix])
                    else:
                        request.fulfill(status=404, body="")

                page.route("**/*", route)
                page.goto("http://127.0.0.1:8000/planos.html")
                page.get_by_role("button", name="Recusar opcionais").click()
                expect(page.locator(".plan-card")).to_have_count(3)
                expect(page.locator(".hero")).to_contain_text("25 a 30/11/2026")
                expect(page.locator(".plan-card-prepaid .price")).to_have_text("R$ 59,90 pelos 3 meses")
                expect(page.locator(".plan-card-featured")).to_contain_text("Profissional Black")
                expect(page.locator(".plan-card-prepaid")).to_contain_text("1.000 páginas por mês")
                if width > 820:
                    expect(page.locator("#menu-toggle")).to_be_hidden()
                else:
                    expect(page.locator("#menu-toggle")).to_be_visible()
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                page.screenshot(path=str(output / f"{viewport}.png"), full_page=True)
                for plan in PLANS:
                    page.goto(f"http://127.0.0.1:8000/checkout.html?plan={plan['code']}")
                    expect(page.locator("#plan-name")).to_have_text(plan["name"])
                    expect(page.locator("#checkout-submit")).to_be_enabled()
                    if plan["billing_period"] == "prepaid_quarter":
                        expect(page.locator("#plan-price")).to_have_text("R$ 59,90 pelos 3 meses")
                        expect(page.locator("#plan-details")).to_contain_text("sem renovação automática")
                        page.screenshot(path=str(output / f"checkout-{viewport}.png"), full_page=True)
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                # Expired or mistyped offer links must never silently buy another plan.
                state["plans"] = [dict(PLANS[0], code="essencial", name="Essencial", price_cents=2990)]
                page.reload()
                expect(page.locator("#plan-name")).to_have_text("Plano indisponível")
                expect(page.locator("#checkout-submit")).to_be_disabled()
                expect(page.locator("#plan-details a")).to_have_attribute("href", "./planos.html")
                page.goto("http://127.0.0.1:8000/planos.html")
                expect(page.locator(".plan-card")).to_have_count(1)
                expect(page.locator("#black-friday-terms")).to_be_hidden()
                assert not errors, errors
                page.close()
                print(f"Black Friday {viewport}: cards, all checkouts, expired link, menu and overflow passed.")
        finally:
            browser.close()


if __name__ == "__main__":
    run()
