"""Check campaign visibility, pricing and checkout with synthetic data and a controlled clock."""
from datetime import datetime, timezone
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


def check_home_promotion(page, state, width, output, viewport):
    page.clock.install(time=datetime(2026, 11, 24, 12, tzinfo=timezone.utc))
    for path in ("/", "/index.html", "/ofx-landing.html"):
        page.goto(f"http://127.0.0.1:8000{path}", wait_until="networkidle")
        expect(page.locator("#black-friday-banner")).to_be_visible()
        expect(page.locator("#black-friday-home-price")).to_contain_text("19,90")
        if width <= 980:
            page.locator("#menu-toggle").click()
        else:
            expect(page.locator("#menu-toggle")).to_be_hidden()
        expect(page.locator("#black-friday-nav-badge")).to_be_visible()
        expect(page.locator("#black-friday-nav-badge").locator("..")).to_have_attribute("href", "./planos.html")
        if width <= 980:
            page.locator("#menu-toggle").click()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        if path == "/":
            page.get_by_role("button", name="Recusar opcionais").click()
            page.screenshot(path=str(output / f"home-{viewport}.png"))
        page.locator("#black-friday-banner a").click()
        expect(page.locator(".plan-card")).to_have_count(3)
        assert urlparse(page.url).path == "/planos.html"

    # The amount must come from an available monthly offer, never the prepaid total.
    state["plans"] = PLANS[1:]
    page.goto("http://127.0.0.1:8000/", wait_until="networkidle")
    expect(page.locator("#black-friday-home-price")).to_contain_text("29,90")
    regular_plans = [dict(PLANS[0], code="essencial", name="Essencial", price_cents=2990)]
    for plans, status in (([PLANS[2]], 200), (regular_plans, 200), ([], 200), (PLANS, 503)):
        state.update(plans=plans, status=status)
        page.reload(wait_until="networkidle")
        expect(page.locator("#black-friday-banner")).to_be_hidden()
        expect(page.locator("#black-friday-nav-badge")).to_be_hidden()
        expect(page.locator("#top-links a[href='./planos.html']")).to_have_count(1)
    state.update(plans=PLANS, status=200)

    # Sale boundaries are Brasília midnight, including expiry with a tab left open.
    page.clock.set_system_time(datetime(2026, 11, 23, 2, 59, 58, tzinfo=timezone.utc))
    page.reload(wait_until="networkidle")
    expect(page.locator("#black-friday-banner")).to_be_hidden()
    page.clock.set_system_time(datetime(2026, 11, 23, 3, tzinfo=timezone.utc))
    page.reload(wait_until="networkidle")
    expect(page.locator("#black-friday-banner")).to_be_visible()
    page.clock.set_system_time(datetime(2026, 12, 1, 2, 59, 58, tzinfo=timezone.utc))
    page.reload(wait_until="networkidle")
    expect(page.locator("#black-friday-banner")).to_be_visible()
    page.clock.fast_forward(2000)
    expect(page.locator("#black-friday-banner")).to_be_hidden()
    expect(page.locator("#black-friday-nav-badge")).to_be_hidden()
    page.reload(wait_until="networkidle")
    expect(page.locator("#black-friday-banner")).to_be_hidden()
    page.clock.set_system_time(datetime(2026, 11, 24, 12, tzinfo=timezone.utc))
    page.goto("http://127.0.0.1:8000/", wait_until="networkidle")
    for check_width in (320, 980, 1024):
        page.set_viewport_size({"width": check_width, "height": 1000})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), check_width
        if check_width > 980:
            expect(page.locator("#menu-toggle")).to_be_hidden()
    page.set_viewport_size({"width": width, "height": 1000})
    print(f"Black Friday home {viewport}: navigation, catalog, boundaries and responsive layout passed.")


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
                        request.fulfill(status=state.get("status", 200), json={"items": state["plans"]})
                    elif path == "/public-config":
                        request.fulfill(json={})
                    elif path in ("/auth/me", "/auth/session/refresh"):
                        request.fulfill(status=401, json={})
                    elif path == "/telemetry/events" or path == "/auth/anonymous-session":
                        request.fulfill(json={"ok": True})
                    elif path == "/":
                        request.fulfill(body=(ROOT / "frontend/index.html").read_bytes(), content_type="text/html")
                    elif Path(path).suffix in (".html", ".js", ".css") and (ROOT / "frontend" / path.lstrip("/")).is_file():
                        request.fulfill(body=(ROOT / "frontend" / path.lstrip("/")).read_bytes(),
                                        content_type={".html": "text/html", ".js": "application/javascript",
                                                      ".css": "text/css"}[Path(path).suffix])
                    else:
                        request.fulfill(status=404, body="")

                page.route("**/*", route)
                check_home_promotion(page, state, width, output, viewport)
                page.goto("http://127.0.0.1:8000/planos.html")
                expect(page.locator(".plan-card")).to_have_count(3)
                expect(page.locator(".hero")).to_contain_text("23 a 30/11/2026")
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
