"""Exercise statement works in Chromium against a disposable real HTTP backend."""

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

import httpx
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def run(frontend):
    from app.application.conversion.contracts.jobs import ConversionJobResultReference
    from app.application.organization_suggestions import build_source_evidence
    from app.dependencies import (
        get_access_control_service,
        get_conversion_batch_service,
    )
    from app.main import app
    from tests.test_conversion_workspace_http_real_api import (
        _run_pro_upload_http_server,
    )

    os.environ["PRO_ORGANIZATION_ENABLED"] = "true"
    os.environ["PRO_STATEMENT_WORKS_ENABLED"] = "true"
    output = ROOT / "tmp/ofx61-browser"
    output.mkdir(parents=True, exist_ok=True)
    with (
        TemporaryDirectory(prefix="gettdone-ofx61-browser-") as temp,
        _run_pro_upload_http_server(Path(temp)) as (backend, publisher, token),
    ):
        access = app.dependency_overrides[get_access_control_service]()
        repository = app.dependency_overrides[get_conversion_batch_service]().repository
        identity = access.resolve_identity(anonymous_fingerprint=None, user_token=token)
        initial_quota = access.get_remaining_quota(identity)
        headers = {
            "Authorization": f"Bearer {token}",
            "Origin": "http://localhost:3000",
            "X-OFX-Pro-Client": "web",
        }
        with httpx.Client(base_url=backend, headers=headers) as api:
            customer = api.post(
                "/api/pro/organization/clients", json={"name": "Empresa Aurora"}
            ).json()
            account = api.post(
                "/api/pro/organization/accounts",
                json={
                    "name": "Conta principal",
                    "client_id": customer["id"],
                    "bank_code": "033",
                    "branch": "0001",
                    "account_number": "00123-4",
                },
            ).json()
            api.post(
                "/api/pro/organization/accounts",
                json={"name": "Conta complementar", "client_id": customer["id"]},
            ).raise_for_status()
            period = api.post(
                "/api/pro/organization/periods",
                json={
                    "name": "Fechamento flexível",
                    "start_date": "2026-09-20",
                    "end_date": "2026-10-19",
                },
            ).json()
            raw = b"date,amount,description\n2026-10-01,12,PIX\n"
            batch = api.post(
                "/api/pro/conversion-batches",
                headers={"Idempotency-Key": "works-browser"},
                json={
                    "files": [
                        {
                            "filename": "extrato.csv",
                            "content_type": "text/csv",
                            "size_bytes": len(raw),
                            "sha256_hex": hashlib.sha256(raw).hexdigest(),
                        }
                    ]
                },
            ).json()
            job = batch["jobs"][0]["job_id"]
            repository.mark_uploaded(job)
            repository.mark_queued(job)
            repository.mark_running(job)
            analysis = {
                "file_type": "csv",
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "opening_balance": 0,
                "closing_balance": 12,
                "bank_code": "033",
                "bank_branch": "0001",
                "account_number": "00123-4",
                "transactions_total": 1,
                "preview_transactions": [
                    {
                        "transaction_id": "tx1",
                        "date": "2026-10-01",
                        "description": "PIX sintético",
                        "amount": 12,
                    }
                ],
            }
            repository.mark_completed(
                job,
                result=ConversionJobResultReference(
                    analysis_id="an_browser_work",
                    payload={
                        "analysis": analysis,
                        "organization_source": build_source_evidence(analysis),
                        "reviewed_at": datetime.now(timezone.utc).isoformat(),
                    },
                ),
            )
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))

                def intercept(route):
                    parsed = urlsplit(route.request.url)
                    if parsed.path == "/auth/me":
                        route.fulfill(
                            json={
                                "user_id": identity.identity_id,
                                "name": "Responsável de teste",
                                "email": "test@example.test",
                                "plan_name": "Piloto Escritório",
                                "quota_remaining": 100,
                                "quota_limit": 100,
                                "quota_mode": "pages",
                            }
                        )
                    elif parsed.path.startswith("/api/pro/"):
                        response = route.fetch(
                            url=backend
                            + parsed.path
                            + ("?" + parsed.query if parsed.query else ""),
                            headers={**route.request.headers, **headers},
                            timeout=30000,
                        )
                        route.fulfill(response=response)
                    elif parsed.path == "/telemetry/events":
                        route.fulfill(status=204)
                    else:
                        route.continue_()

                page.route("**/*", intercept)
                try:
                    page.goto(frontend + "/works")
                    expect(page.get_by_label("Cliente do trabalho")).to_have_value(
                        customer["id"]
                    )
                    page.get_by_label("Período do trabalho").select_option(period["id"])
                    page.get_by_role("button", name="Criar ou abrir trabalho").click()
                    expect(
                        page.get_by_role(
                            "heading", name="Empresa Aurora · Fechamento flexível"
                        )
                    ).to_be_visible()
                    work_id = page.url.rsplit("/", 1)[-1]
                    path = f"/api/pro/works/{work_id}"
                    page.get_by_role(
                        "button", name="Contas e cobertura", exact=True
                    ).click()
                    expect(
                        page.get_by_role("checkbox", name="Conta principal", exact=True)
                    ).not_to_be_checked()
                    page.get_by_role(
                        "checkbox", name="Conta principal", exact=True
                    ).check()
                    page.get_by_role(
                        "checkbox", name="Conta complementar", exact=True
                    ).check()
                    page.get_by_role(
                        "button", name="Confirmar contas do trabalho"
                    ).click()
                    expect(
                        page.get_by_text("Conta principal · Ausente")
                    ).to_be_visible()
                    page.get_by_role(
                        "button", name="Extratos e movimentações", exact=True
                    ).click()
                    page.get_by_role(
                        "button", name="Selecionar conversões existentes"
                    ).click()
                    page.get_by_role("button", name="Vincular ao trabalho").click()
                    page.get_by_role(
                        "button", name="Contas e cobertura", exact=True
                    ).click()
                    doc = page.get_by_role("article", name="extrato.csv")
                    doc.get_by_label("Conta de origem de extrato.csv").select_option(
                        account["id"]
                    )
                    doc.get_by_role("button", name="Salvar conta do extrato").click()
                    doc.get_by_role(
                        "checkbox", name="Conferi o titular e a conta de origem"
                    ).check()
                    doc.get_by_label("Início do documento").fill("2026-09-20")
                    doc.get_by_label("Fim do documento").fill("2026-10-19")
                    doc.get_by_role(
                        "button", name="Salvar conferência documental"
                    ).click()
                    expect(
                        page.get_by_text("Conta principal · Completa")
                    ).to_be_visible()
                    doc.get_by_role("link", name="Abrir revisão").click()
                    page.get_by_role(
                        "link", name="Voltar ao trabalho", exact=True
                    ).click()
                    page.get_by_role(
                        "button", name="Resumo e exportação", exact=True
                    ).click()
                    page.get_by_label("Ressalvas de Conta complementar").fill(
                        "Extrato complementar ainda não recebido; trabalho parcial."
                    )
                    page.get_by_role(
                        "checkbox",
                        name="Conferi o escopo, os documentos, a revisão e as ressalvas",
                    ).check()
                    page.get_by_role(
                        "button", name="Concluir trabalho", exact=True
                    ).click()
                    expect(
                        page.get_by_text(
                            "Versão 1 · Concluído com ressalvas", exact=False
                        )
                    ).to_be_visible()
                    snapshot = api.get(path).json()["closings"][0]
                    with page.expect_download() as download:
                        page.get_by_role(
                            "link", name="Baixar movimentações do recorte (CSV)"
                        ).click()
                    assert (
                        Path(download.value.path())
                        .read_text(encoding="utf-8-sig")
                        .count("PIX sintético")
                        == 1
                    )
                    repository.update_analysis(
                        job,
                        analysis={
                            **analysis,
                            "updated_at": (
                                datetime.now(timezone.utc) + timedelta(seconds=1)
                            ).isoformat(),
                        },
                    )
                    page.get_by_role("button", name="Atualizar trabalhos").click()
                    expect(
                        page.get_by_text(
                            "Os documentos ou vínculos mudaram após a conclusão.",
                            exact=False,
                        )
                    ).to_be_visible()
                    assert api.get(path).json()["closings"][0] == snapshot
                    expect(
                        page.get_by_role(
                            "link", name="Baixar movimentações do recorte (CSV)"
                        )
                    ).to_have_count(0)
                    page.get_by_label("Motivo da reabertura").fill(
                        "Conferir atualização do extrato"
                    )
                    page.get_by_role("button", name="Reabrir trabalho").click()
                    repository.mark_reviewed(
                        job, reviewed_at=datetime.now(timezone.utc).isoformat()
                    )
                    page.get_by_role("button", name="Atualizar trabalhos").click()
                    page.get_by_label("Ressalvas de Conta complementar").fill(
                        "Ainda aguardando extrato complementar; escopo parcial."
                    )
                    page.get_by_role(
                        "checkbox",
                        name="Conferi o escopo, os documentos, a revisão e as ressalvas",
                    ).check()
                    page.get_by_role(
                        "button", name="Concluir trabalho", exact=True
                    ).click()
                    expect(
                        page.get_by_text(
                            "Versão 2 · Concluído com ressalvas", exact=False
                        )
                    ).to_be_visible()
                    assert api.get(path).json()["closings"][1] == snapshot
                    page.screenshot(path=str(output / "desktop.png"), full_page=True)
                    page.set_viewport_size({"width": 390, "height": 844})
                    page.get_by_role(
                        "button", name="Contas e cobertura", exact=True
                    ).click()
                    expect(page.locator(".mobile-menu")).to_be_visible()
                    assert page.evaluate(
                        "document.documentElement.scrollWidth <= window.innerWidth"
                    ), "Mobile overflow"
                    page.screenshot(path=str(output / "mobile.png"), full_page=True)
                    assert not errors, errors
                    assert (
                        access.get_remaining_quota(identity) == initial_quota
                        and publisher.calls == []
                    )
                    (output / "summary.json").write_text(
                        json.dumps(
                            {
                                "result": "PASS",
                                "revision_count": 2,
                                "browser_errors": errors,
                                "scenarios": [
                                    "scope confirmation",
                                    "existing conversion",
                                    "coverage",
                                    "review return",
                                    "reservations",
                                    "CSV cut",
                                    "immutable history",
                                    "reassessment",
                                    "reopening",
                                    "mobile",
                                ],
                            },
                            indent=2,
                        ),
                        encoding="utf-8",
                    )
                    print(
                        "Statement works browser PASS: real HTTP, desktop/mobile, coverage, reservations, CSV, review return and immutable revisions."
                    )
                finally:
                    browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:3101")
    run(parser.parse_args().url.rstrip("/"))
