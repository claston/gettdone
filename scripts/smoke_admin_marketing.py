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
                campaign_calls = []
                campaign_release = asyncio.Event()
                campaign = {"prepared": False, "eligible": 2, "total": 0, "pending": 0, "sent": 0,
                            "skipped": 0, "review": 0, "busy": False,
                            "subject": "Antes de converter: 4 detalhes para conferir no seu extrato"}

                async def route(request):
                    path = urlparse(request.request.url).path
                    if path == "/admin/me":
                        await request.fulfill(json={"is_admin": True})
                    elif path == "/admin/dashboard":
                        await request.fulfill(json={})
                    elif path == "/admin/marketing-contacts":
                        await request.fulfill(json={"items": [], "total": 0})
                    elif path == "/admin/marketing/statement-guide":
                        await request.fulfill(json=campaign)
                    elif path == "/admin/marketing/statement-guide/prepare":
                        assert request.request.post_data_json == {"expected_eligible": 2}
                        campaign.update(prepared=True, total=2, pending=2)
                        await request.fulfill(json=campaign)
                    elif path == "/admin/marketing/statement-guide/send-next":
                        campaign_calls.append(request.request.post_data_json)
                        attempt = len(campaign_calls)
                        if attempt == 1:
                            await campaign_release.wait()
                        if attempt == 2:
                            await request.fulfill(status=502, json={"detail": "Envio interrompido. Retome para verificar sem duplicar."})
                        else:
                            campaign.update(pending=campaign["pending"] - 1, sent=campaign["sent"] + 1)
                            await request.fulfill(json=campaign)
                    elif path == "/admin/marketing/test-email":
                        calls.append(request.request.post_data_json)
                        attempt = len(calls)
                        if attempt == 1:
                            await release.wait()
                        if attempt == 2:
                            await request.fulfill(status=502, json={"detail": "Falha simulada do Resend."})
                        else:
                            await request.fulfill(json={
                                "recipient": "marketing-test@example.com", "template_id": calls[-1]["template_id"],
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
                template = page.locator("#marketing-test-template")
                await expect(template).to_have_value("01-organizacao")
                await button.click()
                await expect(button).to_be_disabled()
                await expect(template).to_be_disabled()
                await expect(status).to_contain_text("Aguardando")
                release.set()
                await expect(status).to_contain_text("Nenhum e-mail foi enviado")
                await expect(button).to_be_enabled()
                await button.click()
                await expect(status).to_contain_text("Falha simulada")
                await expect(button).to_be_enabled()
                await button.click()
                await expect(status).to_contain_text("Envio aceito pelo Resend para marketing-test@example.com")
                assert calls[0]["request_id"] != calls[1]["request_id"]
                assert calls[1]["request_id"] == calls[2]["request_id"], "Retry must keep the same idempotency key"
                assert all(call["template_id"] == "01-organizacao" for call in calls)
                await template.select_option("marketing-test-v1")
                await button.click()
                await expect(button).to_be_enabled()
                assert calls[-1]["template_id"] == "marketing-test-v1"
                assert calls[-1]["request_id"] != calls[-2]["request_id"]
                campaign_send = page.locator("#marketing-campaign-send")
                campaign_status = page.locator("#marketing-campaign-status")
                await expect(page.locator("#marketing-campaign-summary")).to_contain_text("2 contatos elegíveis")
                await expect(campaign_send).to_be_disabled()
                await page.locator("#marketing-campaign-prepare").click()
                await expect(campaign_send).to_be_enabled()
                assert not campaign_calls, "Preview and preparation must never send emails"
                await campaign_send.click()
                await expect(campaign_send).to_be_disabled()
                await expect(page.locator("#marketing-campaign-refresh")).to_be_disabled()
                await page.locator("#marketing-campaign-pause").click()
                campaign_release.set()
                await expect(campaign_status).to_contain_text("Envio pausado")
                assert len(campaign_calls) == 1
                await campaign_send.click()
                await expect(campaign_status).to_contain_text("Envio interrompido")
                await expect(campaign_send).to_be_enabled()
                await campaign_send.click()
                await expect(campaign_status).to_contain_text("Envio concluído")
                await expect(campaign_send).to_be_disabled()
                assert len(campaign_calls) == 3
                assert all(call == {"confirmed": True} for call in campaign_calls)
                await page.reload()
                await page.get_by_role("tab", name="Marketing", exact=True).click()
                await expect(page.locator("#marketing-campaign-summary")).to_contain_text("Aceitos pelo provedor: 2")
                await expect(campaign_send).to_be_disabled()
                assert not errors, errors
                assert await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "Page overflow"
                await page.locator("#admin-marketing-card").screenshot(path=str(output / f"{name}.png"))
                print(json.dumps({"viewport": name, "attempts": len(calls), "retry_same_key": True,
                                  "campaign_pause_resume_reload": True, "page_errors": errors}))
                await page.close()
        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
