"""Exercise order confirmations and lost sales with fictional browser data only."""

import asyncio
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.async_api import async_playwright, expect

ROOT = Path(__file__).resolve().parents[1]


async def main():
    output = ROOT / "tmp/admin-orders"
    output.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            for viewport, width in [("desktop", 1440), ("mobile", 390)]:
                page = await browser.new_page(viewport={"width": width, "height": 950})
                errors, calls, dialogs = [], [], []
                page.on("pageerror", lambda error: errors.append(str(error)))
                state = {"accept": False, "fail_send": False, "fail_lose": False}
                resume = asyncio.Event()
                order = dict(intent_id="chk_fictional", customer_name="Maria Silva", customer_email="maria@example.invalid",
                             plan_name="Profissional", price_cents=3990, status="REQUESTED", next_step="SEND_PAYMENT_LINK",
                             created_at="2026-11-14T12:00:00Z", receipt_status="failed")

                async def dialog_handler(dialog):
                    dialogs.append(dialog.message)
                    assert "Maria Silva" in dialog.message and "maria@example.invalid" in dialog.message
                    assert "Profissional" in dialog.message
                    await (dialog.accept() if state["accept"] else dialog.dismiss())

                page.on("dialog", dialog_handler)

                async def route(route):
                    parsed = urlparse(route.request.url)
                    path = parsed.path
                    if path == "/admin/me":
                        await route.fulfill(json={"is_admin": True})
                    elif path == "/admin/dashboard":
                        await route.fulfill(json={})
                    elif path == "/admin/checkout/intents":
                        selected = parse_qs(parsed.query).get("status", ["open"])[0]
                        items = [order] if selected == "all" or (selected == "lost") == (order["status"] == "LOST") else []
                        await route.fulfill(json={"items": items, "total": len(items)})
                    elif path.endswith("/payment-link"):
                        calls.append("send")
                        assert route.request.post_data_json["payment_link"] == "https://pay.example/fictional"
                        await resume.wait()
                        if state["fail_send"]:
                            await route.fulfill(status=502, json={"detail": "Falha simulada no envio."})
                        else:
                            order.update(status="AWAITING_PAYMENT", next_step="WAIT_FOR_PAYMENT")
                            await route.fulfill(json=order)
                    elif path.endswith("/lose"):
                        calls.append("lose")
                        if state["fail_lose"]:
                            await route.fulfill(status=409, json={"detail": "Aguarde o envio em andamento."})
                        else:
                            order.update(status="LOST", next_step="ORDER_CLOSED")
                            await route.fulfill(json=order)
                    elif Path(path).suffix in (".html", ".js", ".css") and (ROOT / "frontend" / path.lstrip("/")).is_file():
                        await route.fulfill(body=(ROOT / "frontend" / path.lstrip("/")).read_bytes(),
                                            content_type={".html": "text/html", ".js": "application/javascript",
                                                          ".css": "text/css"}[Path(path).suffix])
                    else:
                        await route.fulfill(status=404, body="")

                await page.route("**/*", route)
                await page.goto("http://127.0.0.1:8099/admin.html")
                await page.get_by_role("tab", name="Pedidos", exact=True).click()
                send = page.get_by_role("button", name="Enviar link", exact=True)
                lose = page.get_by_role("button", name="Marcar como venda perdida", exact=True)
                await page.locator('[data-action="payment-link-input"]').fill("https://pay.example/fictional")
                await send.click()
                assert calls == [] and len(dialogs) == 1
                state.update(accept=True, fail_send=True)
                await send.click()
                await expect(send).to_be_disabled()
                # A second dispatch during delivery must not send another email or confirmation.
                await send.dispatch_event("click")
                assert calls == ["send"] and len(dialogs) == 2
                resume.set()
                await expect(page.locator("#admin-status")).to_contain_text("Falha simulada")
                await expect(send).to_be_enabled()
                state["fail_send"] = False
                await send.click()
                await expect(page.locator("#orders-list .order-card .badge")).to_have_text("Aguardando pagamento")
                assert calls == ["send", "send"]
                state["accept"] = False
                await lose.click()
                assert calls == ["send", "send"]
                state.update(accept=True, fail_lose=True)
                await lose.click()
                await expect(page.locator("#admin-status")).to_contain_text("Aguarde o envio")
                await expect(lose).to_be_enabled()
                state["fail_lose"] = False
                await lose.click()
                await expect(page.locator("#orders-list .order-card")).to_have_count(0)
                await page.locator("#orders-filter").select_option("lost")
                await expect(page.locator("#orders-list .order-card .badge")).to_have_text("Venda perdida")
                await expect(send).to_be_disabled()
                await expect(page.get_by_role("button", name="Liberar plano", exact=True)).to_be_disabled()
                await expect(lose).to_have_count(0)
                await expect(page.get_by_role("button", name="Reenviar avisos do pedido")).to_have_count(0)
                await expect(page.get_by_role("button", name="Ver histórico")).to_be_enabled()
                assert not errors, errors
                assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "Horizontal overflow"
                await page.screenshot(path=str(output / f"{viewport}.png"), full_page=True)
                await page.close()
                print(f"{viewport}: order confirmations, retries, duplicate clicks and lost sales passed")
        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
