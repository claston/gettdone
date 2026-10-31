"""Synthetic desktop/mobile acceptance for OFX-62 using the production editor."""

import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import expect, sync_playwright


def run(base):
    output = Path("tmp/pro-organization")
    output.mkdir(parents=True, exist_ok=True)
    clients = [{"id": "client_browser", "name": "Padaria São José", "tax_id": "12345678000190", "trade_name": "",
                "updated_at": "v1", "historical_count": 5, "available_count": 2, "expired_count": 3,
                "attention_count": 1, "accounts": [
                    {"id": "a1", "bank_code": "341", "account_number": "••1234", "statement_count": 3},
                    {"id": "a2", "bank_code": "077", "account_number": "••5678", "statement_count": 2}]}]
    pending = [{"job_id": "job_pending", "batch_id": "batch_pending", "filename": "identificar.pdf", "version": 0,
                "source": {"holder_name": "Titular a conferir", "references": [{"page": 1, "line": 2}]}}]
    jobs = [{"job_id": f"job_{index}", "source_batch_id": f"batch_{index}", "filename": f"extrato-{index}.csv",
             "status": "completed", "expires_at": "2099-01-01T00:00:00Z", "analysis_id": f"an_{index}",
             "result": {"analysis": {"file_type": "csv", "bank_name": "Itaú" if index == 1 else "Inter",
                                      "updated_at": "2026-10-28T12:00:00Z", "preview_transactions": [
                 {"transaction_id": f"tx_{index}", "date": "2026-10-01", "description": f"PIX origem {index}", "amount": 12,
                  "warning_types": ["ambiguous_amount"] if index == 2 else []}]}}}
            for index in (1, 2)]
    writes, errors, held_lists, held_refreshes = [], [], [], []
    loading = {"hold_list": True, "hold_refresh": True}

    def intercept(route):
        path = urlsplit(route.request.url).path
        method = route.request.method
        if path == "/auth/me":
            route.fulfill(json={"user_id": "synthetic", "name": "Usuário Sintético", "email": "synthetic@example.test"})
        elif path == "/api/pro/receipts":
            assert parse_qs(urlsplit(route.request.url).query).get("sort") == ["name"]
            if loading["hold_list"]:
                held_lists.append(route)
                return
            route.fulfill(json={"totals": {"statements": 3, "ready": 2, "attention": 1}, "clients": clients,
                                "pending": pending, "next_cursor": None})
        elif path == "/api/pro/receipts/refresh":
            if loading["hold_refresh"]:
                held_refreshes.append(route)
                return
            route.fulfill(json={"next_cursor": None})
        elif path == "/api/pro/receipts/clients/client_browser" and method == "PATCH":
            body = route.request.post_data_json
            writes.append(path)
            assert body["expected_updated_at"] == clients[0]["updated_at"]
            clients[0].update(name=body["name"], updated_at="v2")
            route.fulfill(json={"id": "client_browser"})
        elif path == "/api/pro/receipts/clients/client_browser/workspace":
            route.fulfill(json={"batch_id": "client_browser", "client_name": clients[0]["name"], "jobs": jobs, "next_cursor": None})
        elif path.endswith("/review"):
            writes.append(path)
            assert "/batch_2/jobs/job_2/review" in path
            route.fulfill(json={"reviewed_at": "2026-10-28T12:00:00Z"})
        elif path == "/api/pro/receipts/clients/client_browser/export":
            route.fulfill(body=b"PK-synthetic", content_type="application/zip", headers={"X-Conversion-Count": "2"})
        elif path == "/api/pro/conversion-batches":
            route.fulfill(json={"items": [], "next_cursor": None})
        elif path == "/api/pro/conversions":
            route.fulfill(json={"items": [], "next_cursor": None})
        elif path.startswith("/telemetry/"):
            route.fulfill(status=204)
        else:
            route.continue_()

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("**/*", intercept)
            page.goto(base + "/dashboard")
            expect(page.get_by_role("heading", name="Extratos recebidos")).to_be_visible()
            expect(page.get_by_role("status")).to_have_text("Carregando extratos...")
            expect(page.get_by_role("button", name="Atualizando extratos...")).to_be_disabled()
            page.screenshot(path=str(output / "received-loading.png"), full_page=True)
            loading["hold_list"] = False
            held_lists.pop().fulfill(json={"totals": {"statements": 3, "ready": 2, "attention": 1}, "clients": clients,
                                          "pending": pending, "next_cursor": None})
            expect(page.get_by_text("2 disponíveis / 5 extratos no histórico")).to_be_visible()
            expect(page.get_by_role("button", name="Atualizando extratos...")).to_be_disabled()
            expect(page.get_by_text("Integração real", exact=True)).to_have_count(0)
            expect(page.get_by_label("Resumo dos extratos").locator("strong")).to_have_text(["3", "2", "1"])
            expect(page.get_by_text("3 expirados", exact=True)).to_be_visible()
            expect(page.get_by_text("Itaú · Conta ••1234")).to_be_visible()
            expect(page.get_by_role("tab", name="Clientes", exact=True)).to_have_attribute("aria-selected", "true")
            expect(page.get_by_role("link", name="Conferir identificar.pdf")).to_have_count(0)
            page.get_by_role("tab", name="Não identificados", exact=True).click()
            expect(page.get_by_role("heading", name="Identificação pendente")).to_be_visible()
            expect(page.get_by_role("link", name="Conferir identificar.pdf")).to_be_visible()
            expect(page.get_by_role("link", name="Padaria São José", exact=False)).to_have_count(0)
            page.screenshot(path=str(output / "received-pending-desktop.png"), full_page=True)
            page.get_by_role("tab", name="Não identificados", exact=True).press("ArrowLeft")
            expect(page.get_by_role("tab", name="Clientes", exact=True)).to_be_focused()
            loading["hold_refresh"] = False
            assert len(held_refreshes) == 1, "Tab switching must not restart synchronization"
            held_refreshes.pop().fulfill(json={"next_cursor": None})
            expect(page.get_by_role("button", name="Atualizar extratos", exact=True)).to_be_enabled()
            expect(page.get_by_role("navigation", name="Navegação principal").get_by_role("link")).to_have_count(2)
            expect(page.locator(".mobile-menu")).not_to_be_visible()
            page.get_by_role("button", name="Editar Padaria São José").click()
            page.get_by_label("Nome do cliente", exact=True).fill("Padaria corrigida")
            page.get_by_role("button", name="Salvar identificação").click()
            expect(page.get_by_role("link", name="Padaria corrigida", exact=False)).to_be_visible()
            page.screenshot(path=str(output / "received-desktop.png"), full_page=True)
            page.get_by_role("link", name="Padaria corrigida", exact=False).click()
            expect(page.get_by_role("heading", name="Extratos do cliente")).to_be_visible()
            options = page.get_by_role("listbox", name="Extratos do cliente").get_by_role("option")
            expect(options).to_have_count(2)
            options.filter(has_text="extrato-2.csv").click()
            expect(page.locator("tr[data-transaction-id='tx_2']")).to_be_visible()
            page.get_by_role("button", name="Marcar revisado", exact=True).click()
            expect(page.get_by_text("Revisado", exact=True)).to_be_visible()
            with page.expect_download():
                page.get_by_role("button", name="Exportar extratos", exact=True).click()
            page.get_by_role("link", name="Extratos recebidos", exact=True).click()
            expect(page.get_by_role("heading", name="Extratos recebidos")).to_be_visible()
            page.set_viewport_size({"width": 390, "height": 844})
            expect(page.locator(".mobile-menu")).to_be_visible()
            expect(page.get_by_role("tab", name="Clientes", exact=True)).to_be_visible()
            expect(page.get_by_role("tab", name="Não identificados", exact=True)).to_be_visible()
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "Mobile overflow"
            page.screenshot(path=str(output / "received-mobile.png"), full_page=True)
            page.get_by_role("tab", name="Não identificados", exact=True).click()
            expect(page.get_by_role("button", name="Conferir identificação", exact=True)).to_be_visible()
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "Pending mobile overflow"
            page.screenshot(path=str(output / "received-pending-mobile.png"), full_page=True)
            page.get_by_role("link", name="Ver conversões e lotes", exact=True).click()
            page.get_by_role("button", name="Todos os arquivos", exact=True).click()
            expect(page.get_by_role("heading", name="Todas as conversões")).to_be_visible()
            expect(page.get_by_text("Integração real", exact=True)).to_have_count(0)
            expect(page.get_by_role("link", name="Cadastrar clientes e contas")).to_have_count(0)
            for legacy in ("/clients", "/periods", "/works", "/works/work_hidden"):
                page.goto(base + legacy)
                expect(page).to_have_url(base + "/dashboard")
            assert len(writes) == 2 and not errors, (writes, errors)
            (output / "summary.json").write_text(json.dumps({"result": "PASS", "writes": writes, "browser_errors": errors,
                "checks": ["initial loading", "background refresh", "accessible tabs", "three KPIs", "clients and subordinate accounts", "inline edit", "pending identity",
                           "flat multi-batch editor", "original batch review", "scoped export", "desktop/mobile",
                           "independent converter", "legacy screens hidden"]},
                ensure_ascii=False, indent=2), encoding="utf-8")
            print("OFX-62 browser PASS: dashboard, inline editing, multi-batch workspace, scoped export and mobile.")
        finally:
            browser.close()
