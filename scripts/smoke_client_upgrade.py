"""Exercise customer upgrades and pending orders with synthetic browser data only."""

from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
PLANS = [
    {"code": code, "name": name, "price_cents": price, "quota_limit": pages,
     "currency": "BRL", "billing_period": "monthly", "version": 1}
    for code, name, price, pages in [
        ("essencial", "Essencial", 2990, 150),
        ("profissional", "Profissional", 3990, 300),
        ("escritorio", "Escritório", 4990, 500),
    ]
]


def order(code="profissional", status="REQUESTED"):
    plan = next(plan for plan in PLANS if plan["code"] == code)
    return {**plan, "intent_id": f"chk_{code}", "plan_code": code, "plan_name": plan["name"],
            "status": status, "created_at": "2026-11-12T12:00:00Z", "payment_link": None,
            "next_step": "SEND_PAYMENT_LINK"}


def run():
    output = ROOT / "tmp/client-upgrade"
    output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            for viewport, width in [("desktop", 1440), ("mobile", 390)]:
                page = browser.new_page(viewport={"width": width, "height": 950})
                page.clock.install()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                state = {"code": "essencial", "orders": [order()], "fail_orders": False, "fail_history": False,
                         "fail_plans": False, "requests": 0}

                def route(request):
                    path = urlparse(request.request.url).path
                    if path == "/auth/me":
                        plan = next((plan for plan in PLANS if plan["code"] == state["code"]), {})
                        request.fulfill(json={"email": "cliente@example.invalid", "name": "Cliente",
                            "plan_code": state["code"], "plan_name": plan.get("name"),
                            "quota_mode": "pages" if state["code"] else "conversion",
                            "quota_remaining": 100, "quota_limit": plan.get("quota_limit", 10),
                            "max_upload_size_bytes": 10485760})
                    elif path == "/plans":
                        request.fulfill(status=503 if state["fail_plans"] else 200, json={"items": PLANS})
                    elif path == "/client/conversions":
                        request.fulfill(status=503 if state["fail_history"] else 200, json={"items": []})
                    elif path == "/checkout/intents/open":
                        state["requests"] += 1
                        request.fulfill(status=503 if state["fail_orders"] else 200,
                                        json={"items": [item for item in state["orders"] if item["status"] != "RELEASED_FOR_USE"]})
                    elif path.startswith("/checkout/intents/"):
                        request.fulfill(status=200 if state["orders"] else 404,
                                        json=state["orders"][0] if state["orders"] else {})
                    elif path == "/telemetry/events":
                        request.fulfill(json={"ok": True})
                    elif Path(path).suffix in (".html", ".js", ".css") and (ROOT / "frontend" / path.lstrip("/")).is_file():
                        content_type = {".html": "text/html", ".js": "application/javascript", ".css": "text/css"}[Path(path).suffix]
                        request.fulfill(body=(ROOT / "frontend" / path.lstrip("/")).read_bytes(), content_type=content_type)
                    else:
                        request.fulfill(status=404, body="")

                page.route("**/*", route)
                page.goto("http://127.0.0.1:8000/client-area.html")
                page.get_by_role("button", name="Recusar opcionais").click()
                expect(page.locator("#upgrade-top")).to_be_visible()
                expect(page.locator("#upgrade-profile")).to_be_visible()
                expect(page.locator("#order-status")).to_be_visible()
                expect(page.get_by_role("link", name="Acompanhar pedido")).to_have_count(0)
                expect(page.get_by_role("button", name="Atualizar status")).to_have_count(0)
                assert page.locator("#order-status").bounding_box()["y"] < page.locator(".quick-metrics").bounding_box()["y"]
                page.locator("#upgrade-top").click()
                expect(page.locator("#upgrade-options")).to_be_visible()
                expect(page.locator("#upgrade-options .upgrade-plan")).to_have_count(2)
                expect(page.locator('#upgrade-options a[href*="intent="]')).to_have_count(0)
                expect(page.locator("#upgrade-options").get_by_text("Pedido em aberto", exact=True)).to_be_visible()
                expect(page.locator('#upgrade-options a[href="./checkout.html?plan=escritorio"]')).to_be_visible()
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "Horizontal overflow"
                page.evaluate("window.scrollTo(0, 0)")
                page.screenshot(path=str(output / f"{viewport}.png"), full_page=True)
                page.locator("#upgrade-close").click()
                expect(page.locator("#upgrade-top")).to_be_focused()
                page.locator("#upgrade-profile").click()
                expect(page.locator("#upgrade-title")).to_be_focused()
                page.locator("#upgrade-close").click()
                expect(page.locator("#upgrade-profile")).to_be_focused()

                # Pending requests receive a payment link without reloading the page.
                state["orders"][0].update(status="AWAITING_PAYMENT", payment_link="https://pay.example.com/synthetic")
                page.clock.run_for(31000)
                expect(page.locator("#order-payment-link")).to_be_visible()
                expect(page.locator("#order-payment-link")).to_have_attribute("href", "https://pay.example.com/synthetic")
                with page.expect_popup() as popup:
                    page.locator("#order-payment-link").click()
                popup.value.close()
                expect(page.locator("#order-status")).to_be_visible()

                # Unsafe links are never offered, including when refreshing automatically.
                state["orders"][0]["payment_link"] = "javascript:alert(1)"
                page.clock.run_for(31000)
                expect(page.locator("#order-payment-link")).to_be_hidden()

                # The API prioritizes payable orders. The banner must instead use creation time,
                # even when the older order has a payment link or a more recent update timestamp.
                state["orders"][0].update(payment_link="https://pay.example.com/older", updated_at="2026-11-12T18:00:00Z")
                newer = {**order("escritorio", "pending"), "created_at": "2026-11-12T13:00:00Z"}
                state["orders"].append(newer)
                page.evaluate("window.dispatchEvent(new Event('focus'))")
                expect(page.locator("#order-intent-id")).to_have_text("chk_escritorio")
                expect(page.locator("#order-plan-name")).to_have_text("Escritório")
                expect(page.locator("#order-status")).not_to_contain_text("Profissional")
                expect(page.locator("#order-payment-link")).to_be_hidden()
                expect(page.locator("#order-status a")).to_have_count(1)
                expect(page.locator("#order-status button")).to_have_count(0)
                # After the newest completes, the next most recent open order becomes visible.
                newer["status"] = "RELEASED_FOR_USE"
                page.clock.run_for(31000)
                expect(page.locator("#order-intent-id")).to_have_text("chk_profissional")
                expect(page.locator("#order-payment-link")).to_have_attribute("href", "https://pay.example.com/older")
                newer["status"] = "REQUESTED"
                page.clock.run_for(31000)
                expect(page.locator("#order-intent-id")).to_have_text("chk_escritorio")

                # A failed refresh retains the visible pending request, then recovers on focus.
                state["fail_orders"] = True
                page.clock.run_for(33000)
                expect(page.locator("#orders-refresh-status")).to_contain_text("Não foi possível")
                expect(page.locator("#order-status")).to_be_visible()
                expect(page.locator("#upgrade-top")).to_be_hidden()
                state["fail_orders"] = False
                state["code"] = "escritorio"
                for item in state["orders"]:
                    item["status"] = "RELEASED_FOR_USE"
                page.evaluate("window.dispatchEvent(new Event('focus'))")
                expect(page.locator("#order-status")).to_be_hidden()
                expect(page.locator("#plan-summary")).to_have_text("Escritório")
                expect(page.locator("#upgrade-top")).to_be_hidden()
                expect(page.locator("#upgrade-profile")).to_be_hidden()
                expect(page.locator("#orders-refresh-status")).to_be_empty()
                # No polling once all requests are complete.
                count = state["requests"]
                page.clock.run_for(61000)
                assert state["requests"] == count

                # Free accounts see every paid plan; intermediate accounts see only higher tiers.
                state["code"] = None
                state["orders"] = []
                page.evaluate("window.dispatchEvent(new Event('focus'))")
                expect(page.locator("#upgrade-top")).to_be_visible()
                page.locator("#upgrade-top").click()
                expect(page.locator("#upgrade-options .upgrade-plan")).to_have_count(3)
                state["code"] = "profissional"
                page.evaluate("window.dispatchEvent(new Event('focus'))")
                expect(page.locator("#upgrade-options .upgrade-plan")).to_have_count(1)
                expect(page.locator("#upgrade-options")).to_contain_text("Escritório")
                state["code"] = "retired-private-plan"
                page.evaluate("window.dispatchEvent(new Event('focus'))")
                expect(page.locator("#upgrade-top")).to_be_hidden()

                # An unavailable history/catalog cannot hide a pending order.
                state.update(code="essencial", orders=[order(status="pending")], fail_history=True, fail_plans=True)
                page.reload()
                expect(page.locator("#order-status")).to_be_visible()
                expect(page.locator("#order-next-step")).to_contain_text("Aguarde o link")
                expect(page.locator("#upgrade-top")).to_be_hidden()
                page.clock.run_for(4000)
                expect(page.locator("#history-rows")).to_contain_text("Não foi possível carregar")
                # Initial order failure is not presented as an empty history or a completed request.
                state.update(fail_orders=True, fail_history=False, fail_plans=False)
                page.reload()
                page.clock.run_for(4000)
                expect(page.locator("#orders-refresh-status")).to_contain_text("Não foi possível")
                expect(page.locator("#orders-rows")).to_contain_text("Não foi possível consultar")
                expect(page.locator("#upgrade-top")).to_be_hidden()
                assert not errors, errors
                page.close()
                print(f"{viewport}: customer upgrade and open order checks passed")
        finally:
            browser.close()


if __name__ == "__main__":
    run()
