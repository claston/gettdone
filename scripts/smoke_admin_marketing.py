"""Exercise manual marketing controls on desktop/mobile without sending real emails."""

import asyncio
import json
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import async_playwright, expect

ROOT = Path(__file__).resolve().parents[1]


async def main():
    output = ROOT / "tmp/admin-marketing"
    output.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            for name, width, height in [("desktop", 1440, 1000), ("mobile", 390, 844)]:
                page = await browser.new_page(viewport={"width": width, "height": height})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                calls = []
                release = asyncio.Event()

                async def route(request):
                    path = urlparse(request.request.url).path
                    if path == "/admin/me":
                        await request.fulfill(json={"is_admin": True})
                    elif path == "/admin/dashboard":
                        await request.fulfill(json={})
                    elif path == "/admin/marketing-contacts":
                        await request.fulfill(json={"items": [], "total": 0})
                    elif path == "/admin/marketing/test-email":
                        calls.append(request.request.post_data_json)
                        attempt = len(calls)
                        if attempt == 1:
                            await release.wait()
                        if attempt == 2:
                            await request.fulfill(status=502, json={"detail": "Falha simulada do Resend."})
                        else:
                            await request.fulfill(json={
                                "recipient": "a@a.com.br", "template_id": "marketing-test-v1",
                                "delivery_mode": "dry_run" if attempt == 1 else "resend",
                                "provider_message_id": "fictional-message-1",
                            })
                    elif path in ("/admin.html", "/admin.js", "/admin.css"):
                        content_type = {".html": "text/html", ".js": "application/javascript", ".css": "text/css"}[Path(path).suffix]
                        await request.fulfill(body=(ROOT / "frontend" / path.lstrip("/")).read_bytes(), content_type=content_type)
                    else:
                        await request.fulfill(status=404, body="")

                await page.route("**/*", route)
                await page.goto("http://127.0.0.1:8099/admin.html")
                await page.get_by_role("tab", name="Marketing", exact=True).click()
                button = page.locator("#marketing-send-test-btn")
                status = page.locator("#marketing-send-status")
                await button.click()
                await expect(button).to_be_disabled()
                await expect(status).to_contain_text("Aguardando")
                release.set()
                await expect(status).to_contain_text("Nenhum e-mail foi enviado")
                await expect(button).to_be_enabled()
                await button.click()
                await expect(status).to_contain_text("Falha simulada")
                await expect(button).to_be_enabled()
                await button.click()
                await expect(status).to_contain_text("Envio aceito pelo Resend para a@a.com.br")
                assert calls[0]["request_id"] != calls[1]["request_id"]
                assert calls[1]["request_id"] == calls[2]["request_id"], "Retry must keep the same idempotency key"
                assert not errors, errors
                assert await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "Page overflow"
                await page.locator("#admin-marketing-card").screenshot(path=str(output / f"{name}.png"))
                print(json.dumps({"viewport": name, "attempts": len(calls), "retry_same_key": True, "page_errors": errors}))
                await page.close()
        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
