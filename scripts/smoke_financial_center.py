"""OFX-65: browser acceptance of the explicitly enabled, synthetic financial demo."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def check_review_consistency(page, base: str, output: Path) -> None:
    """Regression cases reported in PR #538, using only the existing reviewer UI."""
    page.set_viewport_size({"width": 1440, "height": 1100})
    page.on("dialog", lambda dialog: dialog.accept())
    page.goto(base + "/demo/financial/single?tab=Lançamentos")
    page.get_by_role("link", name="Abrir no revisor", exact=True).first.click()
    for description in ("PIX recebido — Cliente Horizonte", "Pagamento fornecedor", "Tarifa de serviços"):
        page.get_by_role("button", name="Remover " + description, exact=True).click()
    expect(page.get_by_role("button", name="Restaurar ", exact=False)).to_have_count(3)
    expect(page.get_by_text("Salvo", exact=True).first).to_be_visible()
    page.get_by_role("link", name="Voltar à Central Financeira", exact=True).click()
    page.get_by_role("button", name="Fechamento", exact=True).click()
    page.get_by_role("checkbox", name="Confirmo estas contas", exact=False).check()
    expect(page.get_by_role("button", name="Simular fechamento", exact=True)).to_be_disabled()
    expect(page.get_by_text("Há documentos que precisam de revisão, mesmo sem lançamentos no recorte.", exact=True)).to_be_visible()
    page.screenshot(path=str(output / "empty-document-pending-review.png"), full_page=True, caret="initial")
    page.get_by_role("link", name="Revisar documento", exact=True).click()
    page.get_by_role("button", name="Marcar revisado", exact=True).click()
    expect(page.get_by_role("button", name="Marcar revisado", exact=True)).to_be_disabled()
    page.get_by_role("link", name="Voltar à Central Financeira", exact=True).click()
    page.get_by_role("checkbox", name="Confirmo estas contas", exact=False).check()
    expect(page.get_by_role("button", name="Simular fechamento", exact=True)).to_be_enabled()
    page.get_by_role("button", name="Simular fechamento", exact=True).click()
    expect(page.get_by_role("status")).to_contain_text("Concluído (simulação)")

    page.goto(base + "/demo/financial/review?tab=Lançamentos")
    page.get_by_role("link", name="Abrir no revisor", exact=True).first.click()
    expect(page.get_by_role("textbox", name="Descrição de Pagamento fornecedor", exact=True)).to_have_value("Pagamento fornecedor")
    page.get_by_role("button", name="Marcar revisado", exact=True).click()
    expect(page.get_by_role("button", name="Marcar revisado", exact=True)).to_be_disabled()
    page.get_by_role("link", name="Voltar à Central Financeira", exact=True).click()
    page.get_by_role("combobox", name="Revisão", exact=True).select_option("pending")
    expect(page.get_by_role("link", name="Abrir no revisor", exact=True)).to_have_count(0)
    page.get_by_role("button", name="Fechamento", exact=True).click()
    page.get_by_role("checkbox", name="Confirmo estas contas", exact=False).check()
    expect(page.get_by_role("button", name="Simular fechamento", exact=True)).to_be_enabled()
    page.screenshot(path=str(output / "suspected-fields-confirmed.png"), full_page=True, caret="initial")
    page.get_by_role("button", name="Lançamentos", exact=True).click()
    page.get_by_role("button", name="Limpar filtros", exact=True).click()
    page.get_by_role("link", name="Abrir no revisor", exact=True).first.click()
    editor = page.get_by_role("textbox", name="Descrição de Pagamento fornecedor", exact=True)
    editor.fill("Pagamento fornecedor conferido novamente")
    page.keyboard.press("Tab")
    expect(page.get_by_text("Salvo", exact=True).first).to_be_visible()
    page.get_by_role("link", name="Voltar à Central Financeira", exact=True).click()
    page.get_by_role("button", name="Fechamento", exact=True).click()
    page.get_by_role("checkbox", name="Confirmo estas contas", exact=False).check()
    expect(page.get_by_role("button", name="Simular fechamento", exact=True)).to_be_disabled()


def run(base: str) -> None:
    output = Path("tmp/ofx65-browser")
    output.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    financial_requests: list[str] = []
    with sync_playwright() as playwright:
        expect.set_options(timeout=30000)
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1100}, device_scale_factor=1)
        page.set_default_timeout(30000)
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("request", lambda request: financial_requests.append(request.url)
                if "/api/pro/" in request.url or "/auth/" in request.url else None)
        page.goto(base + "/demo/financial/clients")
        expect(page.get_by_role("heading", name="Clientes", exact=True)).to_be_visible()
        page.screenshot(path=str(output / "clients-desktop.png"), full_page=True, caret="initial")
        page.get_by_role("link", name="Mercado Horizonte", exact=False).click()
        expect(page.get_by_role("heading", name="Mercado Horizonte", exact=True)).to_be_visible()
        expect(page.get_by_role("button", name="03/09/2026: 2 movimentações, com pendências", exact=True)).to_be_visible()
        expect(page.get_by_role("button", name="Abrir menu", exact=True)).not_to_be_visible()
        page.screenshot(path=str(output / "calendar-desktop.png"), full_page=True, caret="initial")
        page.get_by_role("button", name="03/09/2026: 2 movimentações, com pendências", exact=True).click()
        expect(page.get_by_role("link", name="Abrir no revisor", exact=True)).to_have_count(2)
        page.get_by_role("button", name="Lançamentos", exact=True).click()
        page.get_by_label("Descrição", exact=True).fill("transferência")
        expect(page.get_by_role("link", name="Abrir no revisor", exact=True)).to_have_count(2)
        page.get_by_role("button", name="Limpar filtros", exact=True).click()
        page.screenshot(path=str(output / "movements-desktop.png"), full_page=True, caret="initial")
        page.get_by_role("link", name="Abrir no revisor", exact=True).first.click()
        expect(page.get_by_role("link", name="Voltar à Central Financeira", exact=True)).to_be_visible()
        expect(page.locator("canvas").first).to_be_visible()
        editor = page.get_by_role("textbox", name="Descrição de PIX recebido — Cliente Horizonte", exact=True)
        editor.fill("Correção demonstrativa no revisor")
        page.keyboard.press("Tab")
        expect(page.get_by_text("Salvo", exact=True).first).to_be_visible()
        page.screenshot(path=str(output / "reviewer-desktop.png"), full_page=True, caret="initial")
        page.get_by_role("link", name="Voltar à Central Financeira", exact=True).click()
        expect(page.get_by_text("Correção demonstrativa no revisor", exact=True)).to_be_visible()
        page.get_by_role("button", name="Conciliação", exact=False).click()
        expect(page.get_by_text("Transferência entre contas sugerida", exact=True)).to_be_visible()
        page.screenshot(path=str(output / "reconciliation-desktop.png"), full_page=True, caret="initial")
        page.goto(base + "/demo/financial/partial")
        page.get_by_role("button", name="Fechamento", exact=True).click()
        button = page.get_by_role("button", name="Simular fechamento com ressalvas", exact=True)
        expect(button).to_be_disabled()
        page.get_by_role("checkbox", name="Confirmo estas contas", exact=False).check()
        page.get_by_label("Justificativa para este mês e contas", exact=True).fill("Inter: segunda quinzena não recebida.")
        button.click()
        expect(page.get_by_role("status")).to_contain_text("Concluído com ressalvas (simulação)")
        page.screenshot(path=str(output / "closing-desktop.png"), full_page=True, caret="initial")
        page.get_by_role("button", name="Relatórios", exact=True).click()
        with page.expect_download() as download_info:
            page.get_by_role("button", name="Baixar CSV demonstrativo", exact=True).click()
        download = download_info.value
        destination = output / download.suggested_filename
        download.save_as(destination)
        csv = destination.read_text(encoding="utf-8-sig")
        assert "DEMONSTRAÇÃO" in csv and "Origem" in csv and "segunda quinzena" in csv
        page.screenshot(path=str(output / "reports-desktop.png"), full_page=True, caret="initial")
        for scenario in ("review", "duplicates", "divergence"):
            page.goto(base + "/demo/financial/" + scenario)
            page.get_by_role("button", name="Fechamento", exact=True).click()
            page.get_by_role("checkbox", name="Confirmo estas contas", exact=False).check()
            page.get_by_label("Justificativa para este mês e contas", exact=True).fill("Não pode dispensar integridade.")
            expect(page.get_by_role("button", name="Simular fechamento", exact=False)).to_be_disabled()
        page.goto(base + "/demo/financial/expired")
        page.get_by_role("button", name="Lançamentos", exact=True).click()
        expect(page.get_by_role("link", name="Abrir no revisor", exact=True)).to_have_count(0)
        page.goto(base + "/demo/financial/trial")
        expect(page.get_by_text("Seu primeiro extrato, organizado no mês", exact=True)).to_be_visible()
        page.screenshot(path=str(output / "trial-desktop.png"), full_page=True, caret="initial")
        page.set_viewport_size({"width": 390, "height": 844})
        page.goto(base + "/demo/financial/partial")
        expect(page.get_by_role("button", name="Abrir menu", exact=True)).to_be_visible()
        page.get_by_role("button", name="Abrir menu", exact=True).click()
        expect(page.get_by_role("navigation", name="Navegação móvel", exact=True)).to_be_visible()
        page.get_by_role("button", name="Fechar menu", exact=True).click()
        page.get_by_role("region", name="Calendário financeiro", exact=True).scroll_into_view_if_needed()
        expect(page.get_by_role("button", name="20/09/2026: 0 movimentações, sem cobertura suficiente", exact=True)).to_be_attached()
        page.screenshot(path=str(output / "calendar-mobile.png"), full_page=True, caret="initial")
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "Page overflows mobile viewport"
        page.get_by_role("button", name="Lançamentos", exact=True).click()
        page.screenshot(path=str(output / "movements-mobile.png"), full_page=True, caret="initial")
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "Transactions overflow mobile viewport"
        check_review_consistency(page, base, output)
        assert not financial_requests, financial_requests
        assert not errors, errors
        browser.close()
    (output / "results.json").write_text(json.dumps({"status": "passed", "browser_errors": errors,
        "real_financial_requests": financial_requests, "checks": ["clients", "calendar", "filters", "shared-reviewer",
        "pdf", "closing", "integrity-blocks", "csv", "expiry", "trial", "desktop-mobile",
        "empty-document-review", "accept-unchanged-suspected-fields", "invalidate-review-after-edit"]}, indent=2), encoding="utf-8")
    print("OFX-65 browser acceptance passed (desktop/mobile, PDF, CSV, closing, PR #538 review regressions, no real financial requests).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:3185")
    run(parser.parse_args().url.rstrip("/"))
