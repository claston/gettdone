"""Synthetic acceptance for clients, documents and the retained client editor.

Run against a production build with NEXT_PUBLIC_API_URL=/ and the internal
organization UI disabled, as in the workspace CI suite.
"""

import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import expect, sync_playwright


def document(identifier, filename, category, client_id, *, expired=False):
    return {
        "job_id": identifier, "batch_id": f"batch_{identifier}", "filename": filename,
        "category": category, "status": "expired" if expired else "completed", "is_expired": expired,
        "created_at": "2026-10-01T12:00:00Z",
        "expires_at": "2020-01-01T00:00:00Z" if expired else "2099-01-01T00:00:00Z",
        "movement_start_date": None, "movement_end_date": None,
        "review_url": None if expired else f"/review/batch_{identifier}/{identifier}",
        "context": {"client_id": client_id, "account_id": None, "period_ids": [], "version": 0},
    }


def run(base):
    output = Path("tmp/pro-organization")
    output.mkdir(parents=True, exist_ok=True)
    clients = [
        {"id": "client_browser", "name": "Padaria São José", "tax_id": "12345678000190",
         "trade_name": "", "updated_at": "v1"},
        {"id": "client_empty", "name": "Cliente sem documentos", "tax_id": None, "trade_name": "", "updated_at": "v1"},
        {"id": "client_expired", "name": "Cliente com arquivo expirado", "tax_id": None,
         "trade_name": "", "updated_at": "v1"},
    ]
    catalog = {"clients": clients, "accounts": [], "periods": []}
    documents = [
        document(f"job_{index}", f"extrato-{index}.ofx", "statements", "client_browser") for index in (1, 2)
    ] + [
        document("receipt", "comprovante.pdf", "receipts", "client_browser"),
        document("unknown", "identificar.pdf", "unclassified", None),
        document("expired", "expirado.ofx", "statements", "client_expired", expired=True),
    ]
    jobs = [{"job_id": f"job_{index}", "source_batch_id": f"batch_job_{index}", "filename": f"extrato-{index}.ofx",
             "status": "completed", "expires_at": "2099-01-01T00:00:00Z", "analysis_id": f"an_{index}",
             "result": {"analysis": {"file_type": "ofx", "bank_name": "Itaú" if index == 1 else "Inter",
                                      "updated_at": "2026-10-28T12:00:00Z", "preview_transactions": [
                 {"transaction_id": f"tx_{index}", "date": "2026-10-01", "description": f"PIX origem {index}", "amount": 12,
                  "warning_types": ["ambiguous_amount"] if index == 2 else []}]}}}
            for index in (1, 2)]
    fiscal = {"features": {"invoices": True, "taxes": False, "revenue": False}, "documents": [], "imports": [],
              "movements": [], "links": [], "suggestions": [], "categories": {}, "movement_count": 0,
              "next_offset": None, "access_expires_at": None, "truncated": False,
              "summary": {"documented_sales": None, "operating_receipts": None, "unclassified_inflows": None,
                          "excluded_inflows": None, "tax_payments": {}, "pending_documents": 0,
                          "coverage": "unknown", "consolidation": "observed", "basis": ""}}
    writes, errors, unexpected, held_catalogs, held_documents, held_refreshes = [], [], [], [], [], []
    loading = {"catalog": True, "documents": True, "refresh": True}
    imported = document("imported", "nota-fiscal.pdf", "invoices", None)

    def document_page(query):
        selected = documents
        if query.get("batch_id"):
            selected = [item for item in selected if item["batch_id"] == query["batch_id"][0]]
        elif query.get("unassigned") == ["true"]:
            selected = [item for item in selected if item["context"]["client_id"] is None]
        elif query.get("client_id"):
            selected = [item for item in selected if item["context"]["client_id"] == query["client_id"][0]]
        category = query.get("category", ["all"])[0]
        if category != "all":
            selected = [item for item in selected if item["category"] == category]
        return {"items": selected, "next_cursor": None}

    def intercept(route):
        url = urlsplit(route.request.url)
        path, method = url.path, route.request.method
        if path.startswith("/api/") and method != "GET":
            assert route.request.headers.get("x-ofx-pro-client") == "web"
            writes.append(path)
        if path == "/auth/me":
            route.fulfill(json={"user_id": "synthetic", "name": "Usuário Sintético", "email": "synthetic@example.test"})
        elif path == "/api/pro/organization/catalog":
            if loading["catalog"]:
                held_catalogs.append(route)
                return
            route.fulfill(json=catalog)
        elif path == "/api/pro/organization/clients" and method == "POST":
            client = {"id": "client_new", "updated_at": "v1", **route.request.post_data_json}
            clients.append(client)
            route.fulfill(status=201, json=client)
        elif path == "/api/pro/receipts/clients/client_browser" and method == "PATCH":
            body = route.request.post_data_json
            assert body["expected_updated_at"] == clients[0]["updated_at"]
            clients[0].update(name=body["name"], updated_at="v2")
            route.fulfill(json={"id": "client_browser"})
        elif path == "/api/pro/documents":
            query = parse_qs(url.query)
            if loading["documents"] and "category" in query:
                held_documents.append(route)
                return
            route.fulfill(json=document_page(query))
        elif path == '/api/pro/documents/retention':
            route.fulfill(json={'read_only': False, 'retained_while_paid': True, 'delete_after': None, 'grace_days': 30})
        elif path.endswith('/preparation'):
            item = next(item for item in documents if f"/{item['job_id']}/" in path)
            route.fulfill(json={'job_id': item['job_id'], 'batch_id': item['batch_id'], 'filename': item['filename'],
                'context': item['context'], 'catalog': catalog, 'suggestions': {'clients': [], 'accounts': [], 'period': None},
                'automatically_associated': False, 'matching': None, 'is_bank': item['category'] == 'statements'})
        elif path.endswith("/fiscal"):
            assert path == "/api/pro/financial-center/clients/client_browser/fiscal"
            route.fulfill(json=fiscal)
        elif path == "/api/pro/receipts/refresh":
            if loading["refresh"]:
                held_refreshes.append(route)
                return
            route.fulfill(json={"next_cursor": None})
        elif path == "/api/pro/receipts/documents/imported/confirm":
            assert route.request.post_data_json == {
                "client_id": clients[0]["id"], "name": clients[0]["name"], "tax_id": clients[0]["tax_id"],
                "expected_version": 0,
            }
            imported["context"].update(client_id=clients[0]["id"], version=1)
            route.fulfill(json={"id": clients[0]["id"]})
        elif path == "/api/pro/organization/contexts/bulk":
            assert route.request.post_data_json == {"items": [{
                "batch_id": imported["batch_id"], "job_id": imported["job_id"],
                "context": {"client_id": clients[0]["id"], "account_id": None, "period_ids": [], "expected_version": 0},
            }]}
            imported["context"].update(client_id=clients[0]["id"], version=1)
            route.fulfill(json={"items": [{"job_id": imported["job_id"], "status": 200, "context": imported["context"]}]})
        elif path == "/api/conversion-runtime":
            route.fulfill(json={"direct_batch_enabled": True, "upload_mode": "direct_s3",
                                "execution_mode": "sqs_lambda", "batch_max_files": 12})
        elif path == "/api/pro/conversion-options":
            route.fulfill(json={"available": False})
        elif path == "/api/pro/conversion-batches" and method == "POST":
            body = route.request.post_data_json
            assert "ocr_mode" not in body, "Document import must use the standard conversion mode"
            assert len(body["files"]) == 1 and body["files"][0]["filename"] == imported["filename"]
            route.fulfill(json={"batch_id": imported["batch_id"], "uploads": [
                {"job_id": imported["job_id"], "url": base + "/synthetic-upload", "fields": {}}]})
        elif path == "/synthetic-upload":
            assert method == "POST" and b"nota-fiscal.pdf" in route.request.post_data_buffer
            assert imported["context"]["client_id"] == clients[0]["id"], "Client must be persisted before upload"
            route.fulfill(status=204)
        elif path == "/api/pro/conversion-batches/batch_imported/submit":
            assert route.request.post_data_json == {}
            documents.append(imported)
            route.fulfill(json={"batch_id": imported["batch_id"], "status": "completed"})
        elif path == "/api/pro/conversion-batches/batch_job_1":
            route.fulfill(json={"batch_id": "batch_job_1", "status": "completed", "jobs": [jobs[0]]})
        elif path == '/api/pro/conversion-batches/batch_imported':
            route.fulfill(json={'batch_id': imported['batch_id'], 'status': 'completed', 'jobs': [
                {**jobs[0], 'job_id': imported['job_id'], 'filename': imported['filename'], 'analysis_id': 'an_imported'}]})
        elif path.endswith('/document'):
            from smoke_pro_pdf_sync import synthetic_pdf
            route.fulfill(content_type='application/pdf', body=synthetic_pdf(1))
        elif path == "/api/pro/receipts/clients/client_browser/workspace":
            route.fulfill(json={"batch_id": "client_browser", "client_name": clients[0]["name"], "jobs": jobs, "next_cursor": None})
        elif path == "/api/pro/conversion-batches/batch_job_2/jobs/job_2/review":
            route.fulfill(json={"reviewed_at": "2026-10-28T12:00:00Z"})
        elif path == "/api/pro/receipts/clients/client_browser/export":
            route.fulfill(body=b"PK-synthetic", content_type="application/zip", headers={"X-Conversion-Count": "2"})
        elif path in ("/api/pro/conversion-batches", "/api/pro/conversions"):
            route.fulfill(json={"items": [], "next_cursor": None})
        elif path.startswith("/telemetry/"):
            route.fulfill(status=204)
        elif path.startswith(("/api/", "/auth/")):
            unexpected.append(f"{method} {path}")
            route.fulfill(status=404, json={"detail": "Unexpected synthetic request"})
        else:
            route.continue_()

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.route("**/*", intercept)
        file_list = page.get_by_label("Arquivos recebidos", exact=True)

        def file_button(filename):
            return file_list.get_by_role("button", name=re.compile(re.escape(filename)), include_hidden=True)

        def expect_files(filenames):
            expect(file_list.get_by_role("button")).to_have_count(len(filenames))
            for filename in filenames:
                expect(file_button(filename)).to_be_visible()

        def select_file(filename):
            file_button(filename).click()
            expect(file_button(filename)).to_have_attribute("aria-pressed", "true")
            expect(page.get_by_role("article")).to_have_count(1)
            expect(page.get_by_role("article", name=filename, exact=True)).to_be_visible()

        try:
            page.goto(base + "/dashboard")
            expect(page.get_by_role("heading", name="Clientes", exact=True)).to_be_visible()
            expect(page.get_by_role("status").filter(has_text="Carregando clientes…")).to_be_visible()
            expect(page.get_by_label("Mês de trabalho")).to_be_visible()
            loading["catalog"] = False
            assert len(held_catalogs) == 1
            held_catalogs.pop().fulfill(json=catalog)
            for client in clients:
                expect(page.get_by_role("link", name=client["name"], exact=True)).to_be_visible()
            navigation = page.get_by_role("navigation", name="Navegação principal")
            expect(navigation.get_by_role("link")).to_have_text(["Clientes", "Documentos", "Conversor"])
            expect(page.locator(".mobile-menu")).to_be_hidden()
            assert not writes, "Listing clients must not trigger synchronization or writes"
            # Keep analytics opted out while exercising the client editor below the banner.
            page.get_by_role("button", name="Recusar opcionais", exact=True).click()
            expect(page.locator("#privacy-consent-banner")).to_be_hidden()
            search = page.get_by_role("searchbox", name="Buscar por nome, CPF ou CNPJ")
            for query in ("pada", "SAO JOSE", "12.345.678/0001-90", "12345678000190"):
                search.fill(query)
                expect(page.get_by_role("link", name="Padaria São José", exact=True)).to_be_visible()
                expect(page.get_by_role("link", name="Cliente sem documentos", exact=True)).to_have_count(0)
            search.fill("inexistente")
            expect(page.get_by_text("Nenhum cliente encontrado.", exact=True)).to_be_visible()
            search.fill("")
            page.get_by_role("button", name="Novo cliente", exact=True).click()
            page.get_by_label("Nome do cliente", exact=True).fill("Novo cliente sem extratos")
            page.get_by_role("button", name="Salvar cliente", exact=True).click()
            expect(page.get_by_role("link", name="Novo cliente sem extratos", exact=True)).to_be_visible()
            page.get_by_role("button", name="Editar Padaria São José", exact=True).click()
            page.get_by_label("Nome do cliente", exact=True).fill("Padaria corrigida")
            page.get_by_role("button", name="Salvar cliente", exact=True).click()
            expect(page.get_by_role("link", name="Padaria corrigida", exact=True)).to_be_visible()
            page.screenshot(path=str(output / "clients-desktop.png"), full_page=True)

            page.get_by_role("link", name="Padaria corrigida", exact=True).click()
            expect(page).to_have_url(base + "/documents?client=client_browser")
            expect(page.get_by_role("status").filter(has_text="Carregando documentos…")).to_be_visible()
            loading["documents"] = False
            assert len(held_documents) == 1
            route = held_documents.pop()
            route.fulfill(json=document_page(parse_qs(urlsplit(route.request.url).query)))
            client_files = ["extrato-1.ofx", "extrato-2.ofx", "comprovante.pdf"]
            expect_files(client_files)
            for filename in client_files:
                select_file(filename)
            page.get_by_role("button", name="Atualizar documentos", exact=True).click()
            expect(page.get_by_role("button", name="Atualizando documentos", exact=True)).to_be_disabled()
            types = page.get_by_role("navigation", name="Tipos de documento")
            types.get_by_role("link", name="Extratos", exact=True).click()
            expect_files(["extrato-1.ofx", "extrato-2.ofx"])
            expect(page.get_by_role("article", name="comprovante.pdf", exact=True)).to_have_count(0)
            assert len(held_refreshes) == 1, "Changing filters must not restart synchronization"
            loading["refresh"] = False
            held_refreshes.pop().fulfill(json={"next_cursor": None})
            expect(page.get_by_role("button", name="Atualizar documentos", exact=True)).to_be_enabled()
            expect(types.get_by_role("link", name="Extratos", exact=True)).to_have_attribute("aria-current", "page")
            select_file("extrato-1.ofx")
            page.get_by_role("article", name="extrato-1.ofx").get_by_role("link", name="Revisar importação").click()
            expect(page.locator("tr[data-transaction-id='tx_1']")).to_be_visible()
            expect(page.get_by_role('combobox', name='Cliente', exact=True)).to_have_value('client_browser')
            page.get_by_role("link", name="Voltar aos documentos", exact=True).click()
            expect(page).to_have_url(base + "/documents?client=client_browser&type=statements&view=files")
            types.get_by_role("link", name="Comprovantes", exact=True).click()
            expect_files(["comprovante.pdf"])
            select_file("comprovante.pdf")
            types.get_by_role("link", name="Notas fiscais", exact=True).click()
            expect_files([])
            expect(page.get_by_role("article")).to_have_count(0)
            expect(page.get_by_label("Importar nota XML ou PDF")).to_have_count(0)
            expect(page.get_by_role("region", name="Notas fiscais importadas")).to_have_count(0)
            expect(page.get_by_role("button", name="Importar documentos", exact=True)).to_be_enabled()
            types.get_by_role("link", name="Todos", exact=True).click()
            expect(types.get_by_role("link", name="Todos", exact=True)).to_have_attribute("aria-current", "page")
            expect_files(client_files)
            page.get_by_text("Trocar cliente", exact=True).click()
            expect(page.get_by_role("combobox", name="Cliente", exact=True)).to_have_value("client_browser")
            page.get_by_role("combobox", name="Cliente", exact=True).select_option("unassigned")
            expect_files(["identificar.pdf"])
            select_file("identificar.pdf")
            types.get_by_role("link", name="Extratos", exact=True).click()
            expect(types.get_by_role("link", name="Extratos", exact=True)).to_have_attribute("aria-current", "page")
            expect_files([])
            expect(page.get_by_role("article")).to_have_count(0)
            page.get_by_role("combobox", name="Cliente", exact=True).select_option("client_expired")
            expect_files([])
            expired = page.get_by_role("article", name="expirado.ofx")
            expect(expired).to_have_count(0)
            expect(page.get_by_text("Nenhum documento encontrado nesta lista com os filtros selecionados.")).to_be_visible()
            page.screenshot(path=str(output / "expired-document.png"), full_page=True)

            # Import any document through the client context, including a fiscal PDF.
            page.get_by_text("Trocar cliente", exact=True).click()
            page.get_by_role("combobox", name="Cliente", exact=True).select_option("client_browser")
            expect(page.get_by_role("heading", name="Padaria corrigida", exact=True)).to_be_visible()
            page.get_by_role("button", name="Importar documentos", exact=True).click()
            upload = page.get_by_role("region", name="Importação de documentos")
            upload.get_by_label("Selecionar arquivos").set_input_files({
                "name": imported["filename"], "mimeType": "application/pdf", "buffer": b"%PDF-synthetic\n"})
            upload.get_by_role("button", name="Importar documentos", exact=True).click()
            expect(page.get_by_role('region', name='Revisão da importação')).to_be_visible()
            expect(page.get_by_role('combobox', name='Cliente', exact=True)).to_have_value('client_browser')
            destination = parse_qs(urlsplit(page.url).query)['return_to'][0]
            assert parse_qs(urlsplit(destination).query) == {
                'client': ['client_browser'], 'batch': ['batch_imported'], 'view': ['delivery']}
            assert imported["context"]["client_id"] == clients[0]["id"]
            assert not any(path.endswith("/confirm") for path in writes)
            expect(page.get_by_role("button", name="Confirmar associação ao cliente", exact=True)).to_have_count(0)
            assert imported["context"]["version"] == 1
            page.get_by_role('link', name='Voltar aos documentos', exact=True).click()
            page.get_by_role("link", name="Ver todos os documentos do cliente", exact=True).click()
            page.get_by_role('button', name='Arquivos', exact=True).click()
            client_files.append(imported["filename"])
            expect_files(client_files)
            select_file(imported["filename"])
            page.screenshot(path=str(output / "documents-desktop.png"), full_page=True)

            # Retained deep links still support reviewing multiple original batches and exporting.
            page.goto(base + "/client-review/client_browser")
            expect(page.get_by_role("heading", name="Revisão de documentos")).to_be_visible()
            options = page.get_by_role("listbox", name="Documentos do envio").get_by_role("option")
            expect(options).to_have_count(2)
            options.filter(has_text="extrato-2.ofx").click()
            expect(page.locator("tr[data-transaction-id='tx_2']")).to_be_visible()
            page.get_by_role("button", name="Marcar revisado", exact=True).click()
            expect(page.get_by_text("Revisado", exact=True)).to_be_visible()
            with page.expect_download():
                page.get_by_role("button", name="Exportar extratos", exact=True).click()
            page.get_by_role("link", name="Voltar aos documentos", exact=True).click()
            expect(page).to_have_url(base + "/documents?client=client_browser&type=statements")

            page.set_viewport_size({"width": 390, "height": 844})
            for path, screen in [("/dashboard", "clients"), ("/documents?client=unassigned", "unassigned"),
                                 ("/documents?client=client_browser", "documents")]:
                page.goto(base + path)
                expect(page.get_by_role("button", name="Abrir menu", exact=True)).to_be_visible()
                if screen == "clients":
                    expect(page.get_by_role("link", name="Cliente sem documentos", exact=True)).to_be_visible()
                else:
                    filenames = ["identificar.pdf"] if screen == "unassigned" else client_files
                    expect_files(filenames)
                    expect(page.get_by_role("article")).to_have_count(0)
                    for filename in filenames:
                        select_file(filename)
                        expect(file_list).to_be_hidden()
                        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "Detail mobile overflow"
                        page.get_by_role("button", name="Voltar aos arquivos", exact=True).click()
                        expect_files(filenames)
                        expect(page.get_by_role("article")).to_have_count(0)
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), f"{screen} mobile overflow"
                page.screenshot(path=str(output / f"{screen}-mobile.png"), full_page=True)
            page.get_by_role("button", name="Importar documentos", exact=True).click()
            expect(page.get_by_role("heading", name="Importar documentos", exact=True)).to_be_visible()
            expect(page.get_by_role("radio", name="Conversão PRO")).to_have_count(0)
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "Import mobile overflow"
            page.get_by_role("button", name="Abrir menu", exact=True).click()
            page.get_by_role("navigation", name="Navegação móvel").get_by_role("link", name="Conversor", exact=True).click()
            expect(page).to_have_url(base + "/conversions")
            page.get_by_role("button", name="Todos os arquivos", exact=True).click()
            expect(page.get_by_role("heading", name="Todas as conversões")).to_be_visible()
            expect(page.get_by_role("link", name="Cadastrar clientes e contas")).to_have_count(0)
            for legacy in ("/clients", "/periods", "/works", "/works/work_hidden"):
                page.goto(base + legacy)
                expect(page).to_have_url(base + "/dashboard")
            assert len(writes) == 9 and not errors and not unexpected, (writes, errors, unexpected)
            summary = {"result": "PASS", "writes": writes, "browser_errors": errors, "unexpected_requests": unexpected,
                       "checks": ["client and document loading", "client roster without documents", "accent and tax ID search",
                                  "client creation and optimistic edit", "explicit refresh across filters", "document categories",
                                  "unassigned documents", "expired documents hidden", "separate fiscal upload hidden",
                                  "file selection and matching detail", "review return preserves filters and files view",
                                  "client-context fiscal PDF import and persisted association", "multi-batch editor compatibility",
                                  "original batch review",
                                  "scoped export", "desktop/mobile", "mobile file detail and back navigation",
                                  "independent converter", "legacy screens hidden"]}
            (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
            print("PRO clients/documents browser PASS: roster, filters, import, confirmation, review, export and mobile.")
        except Exception:
            page.screenshot(path=str(output / "received-failure.png"), full_page=True)
            print("Browser errors:", errors, "Unexpected requests:", unexpected)
            print(page.locator("body").aria_snapshot())
            raise
        finally:
            browser.close()
