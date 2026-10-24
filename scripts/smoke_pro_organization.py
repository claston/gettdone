"""Synthetic browser acceptance for manual PRO organization, desktop and mobile."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import expect, sync_playwright


def run(base: str) -> None:
    output = Path("tmp/pro-organization")
    output.mkdir(parents=True, exist_ok=True)
    catalog = {"clients": [], "accounts": [], "periods": []}
    files = [{"job_id": f"job-{index}", "batch_id": "batch-browser", "filename": filename,
        "status": "expired" if index == 3 else "completed", "is_expired": index == 3,
        "created_at": "2026-10-01T12:00:00Z", "movement_start_date": None, "movement_end_date": None,
        "review_url": None if index == 3 else f"/review/batch-browser/job-{index}",
        "context": {"client_id": None, "account_id": None, "period_ids": [], "version": 0}}
        for index, filename in enumerate(["extrato-semana.pdf", "extrato-mensal.csv", "arquivo-expirado.pdf"], 1)]
    writes = []
    errors = []

    def intercept(route):
        url = urlsplit(route.request.url)
        if url.path == "/auth/me":
            route.fulfill(json={"user_id": "synthetic-owner", "name": "Cliente de demonstração", "email": "test@example.test",
                "plan_name": "Pro Escritório", "quota_remaining": 4990, "quota_limit": 5000, "quota_mode": "pages"})
            return
        if url.path == "/api/pro/conversion-batches":
            route.fulfill(json={"items": [], "next_cursor": None})
            return
        prefix = "/api/pro/organization/"
        if not url.path.startswith(prefix):
            route.continue_()
            return
        method = route.request.method
        path = url.path.removeprefix(prefix)
        if method != "GET":
            assert route.request.headers.get("x-ofx-pro-client") == "web"
            body = route.request.post_data_json
            assert not {"owner_user_id", "organization_id", "user_token"} & body.keys()
            writes.append(path)
        if path == "catalog":
            route.fulfill(json=catalog)
        elif path == "conversions":
            query = parse_qs(url.query)
            selected = [item for item in files if
                (not query.get("unassigned") or not item["context"]["client_id"])
                and (not query.get("client_id") or item["context"]["client_id"] == query["client_id"][0])
                and (not query.get("account_id") or item["context"]["account_id"] == query["account_id"][0])
                and (not query.get("period_id") or query["period_id"][0] in item["context"]["period_ids"])]
            route.fulfill(json={"items": selected, "next_cursor": None})
        elif path in catalog and method == "POST":
            item = {"id": f"{path}-1", **body}
            catalog[path].append(item)
            route.fulfill(status=201, json=item)
        elif path.endswith("/context") and method == "PUT":
            item = next(item for item in files if item["job_id"] == path.split("/")[-2])
            if item["context"]["version"] != body["expected_version"]:
                route.fulfill(status=409, json={"detail": "Atualize a lista antes de salvar novamente."})
            else:
                item["context"] = {key: body[key] for key in ["client_id", "account_id", "period_ids"]}
                item["context"]["version"] = body["expected_version"] + 1
                route.fulfill(json=item["context"])
        else:
            route.fulfill(status=404, json={"detail": "Unexpected synthetic request"})

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("**/*", intercept)
            page.goto(base + "/clients")
            expect(page.get_by_role("heading", name="Clientes e contas")).to_be_visible()
            client_form = page.get_by_role("form", name="Cadastro de cliente")
            client_form.get_by_label("Nome do cliente", exact=True).fill("Empresa Aurora")
            client_form.get_by_label("CPF/CNPJ (opcional)").fill("12.345.678/0001-90")
            client_form.get_by_label("Nome fantasia (opcional)").fill("Aurora")
            client_form.get_by_role("button", name="Salvar cadastro").click()
            expect(page.get_by_text("Cliente salvo.", exact=True)).to_be_visible()
            page.get_by_role("button", name="Contas", exact=True).click()
            account_form = page.get_by_role("form", name="Cadastro de conta")
            account_form.get_by_label("Nome da conta", exact=True).fill("Conta principal")
            account_form.get_by_label("Cliente da conta").select_option(label="Empresa Aurora")
            account_form.get_by_label("Número da conta (opcional)").fill("00012345678901234567-8")
            account_form.get_by_role("button", name="Salvar cadastro").click()
            expect(page.get_by_text("Conta salva.", exact=True)).to_be_visible()
            page.get_by_role("button", name="Períodos", exact=True).click()
            period_form = page.get_by_role("form", name="Cadastro de período")
            period_form.get_by_label("Nome do período", exact=True).fill("Fechamento especial")
            period_form.get_by_label("Data inicial").fill("2026-09-20")
            period_form.get_by_label("Data final").fill("2026-10-04")
            period_form.get_by_role("button", name="Salvar cadastro").click()
            expect(page.get_by_text("Período salvo.", exact=True)).to_be_visible()
            file = page.get_by_role("article", name="extrato-semana.pdf")
            file.get_by_role("button", name="Organizar", exact=True).click()
            file.get_by_role("checkbox", name="Fechamento especial", exact=False).check()
            file.get_by_role("button", name="Salvar organização").click()
            expect(file.get_by_text("Organização salva.", exact=True)).to_be_visible()
            page.get_by_label("Filtrar por cliente").select_option("unassigned")
            page.get_by_label("Filtrar por período").select_option("periods-1")
            expect(page.get_by_role("article", name="extrato-mensal.csv")).to_have_count(0)
            file = page.get_by_role("article", name="extrato-semana.pdf")
            expect(file).to_be_visible()
            file.get_by_role("button", name="Organizar", exact=True).click()
            file.get_by_label("Cliente do arquivo").select_option("clients-1")
            file.get_by_label("Conta do arquivo").select_option("accounts-1")
            file.get_by_role("button", name="Salvar organização").click()
            expect(page.get_by_text("Organização salva. O arquivo pode ser encontrado nos novos filtros.")).to_be_visible()
            expect(page.get_by_role("article", name="extrato-semana.pdf")).to_have_count(0)
            page.reload()
            file = page.get_by_role("article", name="extrato-semana.pdf")
            expect(file).to_be_visible()
            expect(file.locator("span", has_text="Empresa Aurora")).to_be_visible()
            expect(file.get_by_role("link", name="Abrir revisão")).to_have_attribute("href", "/review/batch-browser/job-1")
            expect(page.get_by_role("article", name="arquivo-expirado.pdf").get_by_role("link", name="Abrir revisão")).to_have_count(0)
            page.screenshot(path=str(output / "desktop.png"), full_page=True)
            page.set_viewport_size({"width": 390, "height": 844})
            page.goto(base + "/periods")
            expect(page.get_by_role("heading", name="Períodos de trabalho", exact=True)).to_be_visible()
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "Mobile horizontal overflow"
            page.screenshot(path=str(output / "mobile.png"), full_page=True)
            assert not errors, errors
            (output / "summary.json").write_text(json.dumps({"writes": writes, "browser_errors": errors,
                "account_number": catalog["accounts"][0]["account_number"], "context_version": files[0]["context"]["version"]},
                ensure_ascii=False, indent=2), encoding="utf-8")
            print("PRO organization browser acceptance passed: desktop, mobile, independent filters, manual association, reload and expiry.")
        finally:
            browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:3100")
    run(parser.parse_args().url.rstrip("/"))
