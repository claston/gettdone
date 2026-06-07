from io import BytesIO

from openpyxl import Workbook

from app.application import analyze_service as analyze_service_module
from app.application.analyze_service import AnalyzeService
from app.application.models import CanonicalTransaction, NormalizedTransaction
from app.application.pdf_layout_inference import PdfLayoutInference
from app.application.pdf_parser import PdfParseResult
from app.application.storage_service import TempAnalysisStorage
from tests.fixtures.pdf_golden_samples import (
    PDF_PARSE_METRICS_GROUPED_CANONICAL_OK,
    PDF_PARSE_METRICS_INLINE_CANONICAL_EMPTY,
    build_pdf_parse_result,
)


def _build_xlsx_bytes(rows: list[list[object]]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    for row in rows:
        sheet.append(row)
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def test_analyze_service_uses_real_xlsx_content(tmp_path) -> None:
    storage = TempAnalysisStorage(root_dir=tmp_path, ttl_seconds=3600)
    service = AnalyzeService(storage=storage)
    raw = _build_xlsx_bytes(
        [
            ["Data", "Descricao", "Valor"],
            ["01/04/2026", "IFOOD", "-58,90"],
            ["02/04/2026", "SALARIO", "2500,00"],
        ]
    )

    result = service.analyze(filename="sample.xlsx", raw_bytes=raw)

    assert result.file_type == "xlsx"
    assert result.transactions_total == 2
    assert result.total_inflows == 2500.00
    assert result.total_outflows == -58.90
    assert result.net_total == 2441.10
    assert result.preview_transactions[0].description == "IFOOD"
    assert result.preview_transactions[1].description == "SALARIO"
    assert result.pdf_processing_metrics is None


def test_analyze_service_uses_real_ofx_content(tmp_path) -> None:
    storage = TempAnalysisStorage(root_dir=tmp_path, ttl_seconds=3600)
    service = AnalyzeService(storage=storage)
    raw = """OFXHEADER:100
DATA:OFXSGML
VERSION:102

<OFX>
  <BANKMSGSRSV1>
    <STMTTRNRS>
      <STMTRS>
        <BANKTRANLIST>
          <STMTTRN>
            <DTPOSTED>20260401120000[-3:BRT]
            <TRNAMT>-58.90
            <MEMO>IFOOD
            <TRNTYPE>DEBIT
          </STMTTRN>
          <STMTTRN>
            <DTPOSTED>20260402120000[-3:BRT]
            <TRNAMT>2500.00
            <NAME>SALARIO
            <TRNTYPE>CREDIT
          </STMTTRN>
        </BANKTRANLIST>
      </STMTRS>
    </STMTTRNRS>
  </BANKMSGSRSV1>
</OFX>
""".encode("utf-8")

    result = service.analyze(filename="sample.ofx", raw_bytes=raw)

    assert result.file_type == "ofx"
    assert result.transactions_total == 2
    assert result.total_inflows == 2500.00
    assert result.total_outflows == -58.90
    assert result.net_total == 2441.10
    assert result.preview_transactions[0].description == "IFOOD"
    assert result.preview_transactions[1].description == "SALARIO"
    assert result.pdf_processing_metrics is None


def test_analyze_service_uses_pdf_content_with_layout_inference(tmp_path, monkeypatch) -> None:
    storage = TempAnalysisStorage(root_dir=tmp_path, ttl_seconds=3600)
    service = AnalyzeService(storage=storage)
    parse_metrics = dict(PDF_PARSE_METRICS_GROUPED_CANONICAL_OK)
    parse_metrics["export_recommendation"] = "review_recommended"
    parse_metrics["export_recommendation_reason"] = "medium_confidence_band"
    monkeypatch.setattr(
        analyze_service_module,
        "parse_pdf_transactions",
        lambda raw_bytes: build_pdf_parse_result(
            transactions=[
                NormalizedTransaction(
                    date="2023-11-06",
                    description="Transferencia recebida pelo Pix CLIENTE A",
                    amount=1069.04,
                    type="inflow",
                ),
                NormalizedTransaction(
                    date="2023-11-14",
                    description="Transferencia enviada pelo Pix FORNECEDOR B",
                    amount=-4000.00,
                    type="outflow",
                ),
            ],
            layout_name="nubank_statement_ptbr",
            confidence=0.94,
            extracted_text="TOTAL DE ENTRADAS\nTOTAL DE SAIDAS\nTRANSFERENCIA RECEBIDA PELO PIX",
            parse_metrics=parse_metrics,
        ),
    )

    result = service.analyze(filename="sample.pdf", raw_bytes=b"%PDF synthetic")

    assert result.file_type == "pdf"
    assert result.transactions_total == 2
    assert result.layout_inference_name is not None
    assert result.layout_inference_confidence is not None
    assert result.layout_inference_confidence >= 0.2
    assert result.semantic_type == "extrato_bancario"
    assert result.semantic_confidence is not None
    assert result.pdf_processing_metrics is not None
    assert result.pdf_processing_metrics.selected_parser == "grouped"
    assert result.pdf_processing_metrics.grouped_transactions_count == 2
    assert result.pdf_processing_metrics.balance_consistency_checked == 1
    assert result.pdf_processing_metrics.balance_consistency_failed == 0
    assert result.pdf_processing_metrics.canonical_transactions_count == 2
    assert result.pdf_processing_metrics.canonical_with_running_balance_count == 2
    assert result.pdf_processing_metrics.canonical_with_external_reference_count == 2
    assert result.pdf_processing_metrics.canonical_warning_count == 0
    assert result.pdf_processing_metrics.canonical_balance_warning_count == 0
    assert result.pdf_processing_metrics.canonical_warning_transactions_count == 0
    assert result.pdf_processing_metrics.canonical_warning_types_count == 0
    assert result.pdf_processing_metrics.canonical_warning_types == ""
    assert result.pdf_processing_metrics.canonical_warning_types_list == []
    assert result.pdf_processing_metrics.canonical_running_balance_coverage_rate == 1.0
    assert result.pdf_processing_metrics.canonical_external_reference_coverage_rate == 1.0
    assert result.pdf_processing_metrics.canonical_warning_transaction_rate == 0.0
    assert result.pdf_processing_metrics.canonical_source_parser_grouped_count == 2
    assert result.pdf_processing_metrics.canonical_source_parser_types == "grouped"
    assert result.pdf_processing_metrics.canonical_source_parser_types_list == ["grouped"]
    assert result.pdf_processing_metrics.total_ms >= 0.0
    assert any(
        insight.type == "pdf_export_review_recommended"
        for insight in result.insights
    )


def test_analyze_service_uses_itau_pdf_inline_rows(tmp_path, monkeypatch) -> None:
    storage = TempAnalysisStorage(root_dir=tmp_path, ttl_seconds=3600)
    service = AnalyzeService(storage=storage)
    parse_metrics = dict(PDF_PARSE_METRICS_INLINE_CANONICAL_EMPTY)
    parse_metrics["export_recommendation"] = "safe_to_export"
    parse_metrics["export_recommendation_reason"] = "high_confidence_band"
    monkeypatch.setattr(
        analyze_service_module,
        "parse_pdf_transactions",
        lambda raw_bytes: build_pdf_parse_result(
            transactions=[
                NormalizedTransaction(
                    date="2026-04-13",
                    description="PIX TRANSF ERICA S13/04",
                    amount=-2835.00,
                    type="outflow",
                ),
                NormalizedTransaction(
                    date="2026-04-09",
                    description="TED 102.0001.ERICA S Y",
                    amount=6000.00,
                    type="inflow",
                ),
            ],
            layout_name="itau_statement_ptbr",
            confidence=1.0,
            extracted_text="EXTRATO CONTA / LANCAMENTOS\nDATA LANCAMENTOS VALOR",
            parse_metrics=parse_metrics,
        ),
    )

    result = service.analyze(filename="itau.pdf", raw_bytes=b"%PDF synthetic")

    assert result.file_type == "pdf"
    assert result.transactions_total == 2
    assert result.layout_inference_name in {"itau_statement_ptbr", "generic_statement_ptbr"}
    assert result.layout_inference_confidence is not None
    assert result.pdf_processing_metrics is not None
    assert result.pdf_processing_metrics.selected_parser == "inline"
    assert result.pdf_processing_metrics.canonical_transactions_count == 2
    assert result.pdf_processing_metrics.canonical_source_parser_inline_count == 2
    assert result.pdf_processing_metrics.canonical_source_parser_types == "inline"
    assert result.pdf_processing_metrics.canonical_source_parser_types_list == ["inline"]
    assert all(
        insight.type != "pdf_export_review_recommended"
        for insight in result.insights
    )


def test_analyze_service_extracts_bank_branch_and_account_from_pdf_text(tmp_path, monkeypatch) -> None:
    storage = TempAnalysisStorage(root_dir=tmp_path, ttl_seconds=3600)
    service = AnalyzeService(storage=storage)
    monkeypatch.setattr(
        analyze_service_module,
        "parse_pdf_transactions",
        lambda raw_bytes: build_pdf_parse_result(
            transactions=[
                NormalizedTransaction(
                    date="2026-04-13",
                    description="PIX RECEBIDO CLIENTE A",
                    amount=1000.00,
                    type="inflow",
                ),
            ],
            layout_name="santander_aplicativo_empresas_conta_corrente_extrato_v1",
            confidence=0.99,
            extracted_text="AGÊNCIA: 1234-5 CONTA: 123456-7",
            parse_metrics=PDF_PARSE_METRICS_GROUPED_CANONICAL_OK,
        ),
    )

    result = service.analyze(filename="bradesco.pdf", raw_bytes=b"%PDF synthetic")

    assert result.bank_branch == "1234-5"
    assert result.account_number == "123456-7"
    assert result.bank_code == "033"


def test_analyze_service_resolves_opening_balance_from_saldo_anterior_row_when_running_balance_missing(
    tmp_path, monkeypatch
) -> None:
    storage = TempAnalysisStorage(root_dir=tmp_path, ttl_seconds=3600)
    service = AnalyzeService(storage=storage)
    monkeypatch.setattr(
        analyze_service_module,
        "parse_pdf_transactions",
        lambda raw_bytes: build_pdf_parse_result(
            transactions=[
                NormalizedTransaction(
                    date="2024-04-01",
                    description="SALDO ANTERIOR",
                    amount=10000.0,
                    type="inflow",
                ),
                NormalizedTransaction(
                    date="2024-04-01",
                    description="PAGAMENTO BOLETO",
                    amount=-150.0,
                    type="outflow",
                ),
            ],
            layout_name="caixa_gerenciador_extrato_por_periodo_v1",
            confidence=0.95,
            extracted_text="SALDO ANTERIOR 10.000,00",
            parse_metrics=PDF_PARSE_METRICS_INLINE_CANONICAL_EMPTY,
        ),
    )

    result = service.analyze(filename="caixa.pdf", raw_bytes=b"%PDF synthetic")

    assert result.opening_balance == 10000.0


def test_analyze_service_prefers_saldo_anterior_row_over_later_running_balance(tmp_path, monkeypatch) -> None:
    storage = TempAnalysisStorage(root_dir=tmp_path, ttl_seconds=3600)
    service = AnalyzeService(storage=storage)
    monkeypatch.setattr(
        analyze_service_module,
        "parse_pdf_transactions",
        lambda raw_bytes, **kwargs: PdfParseResult(
            transactions=[
                NormalizedTransaction(
                    date="2024-04-01",
                    description="SALDO ANTERIOR",
                    amount=10000.0,
                    type="inflow",
                ),
                NormalizedTransaction(
                    date="2024-04-01",
                    description="PAGAMENTO BOLETO",
                    amount=-150.0,
                    type="outflow",
                ),
            ],
            layout=PdfLayoutInference(
                layout_name="caixa_gerenciador_extrato_por_periodo_v1",
                confidence=0.95,
                used_fallback=False,
            ),
            extracted_text="SALDO ANTERIOR 10.000,00",
            parse_metrics=PDF_PARSE_METRICS_INLINE_CANONICAL_EMPTY,
            canonical_transactions=[
                CanonicalTransaction(
                    date="2024-04-01",
                    description="SALDO ANTERIOR",
                    amount=10000.0,
                    type="inflow",
                    running_balance=None,
                    source_page=1,
                    source_line=1,
                    layout_name="caixa_gerenciador_extrato_por_periodo_v1",
                    confidence=0.95,
                    source_parser="grouped",
                ),
                CanonicalTransaction(
                    date="2024-04-01",
                    description="PAGAMENTO BOLETO",
                    amount=-150.0,
                    type="outflow",
                    running_balance=9850.0,
                    source_page=1,
                    source_line=2,
                    layout_name="caixa_gerenciador_extrato_por_periodo_v1",
                    confidence=0.95,
                    source_parser="grouped",
                ),
            ],
        ),
    )

    result = service.analyze(filename="caixa.pdf", raw_bytes=b"%PDF synthetic")

    assert result.opening_balance == 10000.0


def test_analyze_service_resolves_opening_balance_from_extracted_text_when_outside_table(
    tmp_path, monkeypatch
) -> None:
    storage = TempAnalysisStorage(root_dir=tmp_path, ttl_seconds=3600)
    service = AnalyzeService(storage=storage)
    monkeypatch.setattr(
        analyze_service_module,
        "parse_pdf_transactions",
        lambda raw_bytes: build_pdf_parse_result(
            transactions=[
                NormalizedTransaction(
                    date="2024-05-02",
                    description="TED RECEBIDA CLIENTE",
                    amount=3000.0,
                    type="inflow",
                ),
            ],
            layout_name="banco_do_nordeste_extrato_conta_corrente_v1",
            confidence=0.95,
            extracted_text="DADOS DA CONTA\nSALDO ANTERIOR R$ 10.000,00\nLANCAMENTOS",
            parse_metrics=PDF_PARSE_METRICS_INLINE_CANONICAL_EMPTY,
        ),
    )

    result = service.analyze(filename="bnb.pdf", raw_bytes=b"%PDF synthetic")

    assert result.opening_balance == 10000.0


def test_analyze_service_does_not_extract_account_from_transaction_body_without_header_label(
    tmp_path, monkeypatch
) -> None:
    storage = TempAnalysisStorage(root_dir=tmp_path, ttl_seconds=3600)
    service = AnalyzeService(storage=storage)
    monkeypatch.setattr(
        analyze_service_module,
        "parse_pdf_transactions",
        lambda raw_bytes: build_pdf_parse_result(
            transactions=[
                NormalizedTransaction(
                    date="2024-05-02",
                    description="TARIFA MANUTENCAO CONTA 990003",
                    amount=-60.0,
                    type="outflow",
                ),
            ],
            layout_name="banco_do_nordeste_extrato_conta_corrente_v1",
            confidence=0.95,
            extracted_text=(
                "LANCAMENTOS\n"
                "02/05 TARIFA MANUTENCAO CONTA 990003 60,00 D\n"
                "03/05 PIX RECEBIDO CLIENTE 300,00 C"
            ),
            parse_metrics=PDF_PARSE_METRICS_INLINE_CANONICAL_EMPTY,
        ),
    )

    result = service.analyze(filename="bnb.pdf", raw_bytes=b"%PDF synthetic")

    assert result.account_number is None


def test_analyze_service_extracts_account_and_branch_from_header_without_colon(
    tmp_path, monkeypatch
) -> None:
    storage = TempAnalysisStorage(root_dir=tmp_path, ttl_seconds=3600)
    service = AnalyzeService(storage=storage)
    monkeypatch.setattr(
        analyze_service_module,
        "parse_pdf_transactions",
        lambda raw_bytes: build_pdf_parse_result(
            transactions=[
                NormalizedTransaction(
                    date="2024-05-02",
                    description="PIX RECEBIDO CLIENTE",
                    amount=300.0,
                    type="inflow",
                ),
            ],
            layout_name="santander_aplicativo_empresas_conta_corrente_extrato_v1",
            confidence=0.95,
            extracted_text=(
                "DADOS DA CONTA\n"
                "AGENCIA 1234-5\n"
                "CONTA 123456-7\n"
                "LANCAMENTOS\n"
                "02/05 PIX RECEBIDO CLIENTE 300,00 C"
            ),
            parse_metrics=PDF_PARSE_METRICS_INLINE_CANONICAL_EMPTY,
        ),
    )

    result = service.analyze(filename="santander.pdf", raw_bytes=b"%PDF synthetic")

    assert result.bank_branch == "1234-5"
    assert result.account_number == "123456-7"


def test_analyze_service_extracts_branch_from_split_header_line_without_inventing_account(
    tmp_path, monkeypatch
) -> None:
    storage = TempAnalysisStorage(root_dir=tmp_path, ttl_seconds=3600)
    service = AnalyzeService(storage=storage)
    monkeypatch.setattr(
        analyze_service_module,
        "parse_pdf_transactions",
        lambda raw_bytes: build_pdf_parse_result(
            transactions=[
                NormalizedTransaction(
                    date="2024-05-02",
                    description="TARIFA MANUTENCAO CONTA 990003",
                    amount=-60.0,
                    type="outflow",
                ),
            ],
            layout_name="banco_do_nordeste_extrato_conta_corrente_v1",
            confidence=0.95,
            extracted_text=(
                "BANCO DO NORDESTE\n"
                "AGENCIA:\n"
                "1234\n"
                "LANCAMENTOS\n"
                "02/05 TARIFA MANUTENCAO CONTA 990003 60,00 D"
            ),
            parse_metrics=PDF_PARSE_METRICS_INLINE_CANONICAL_EMPTY,
        ),
    )

    result = service.analyze(filename="bnb.pdf", raw_bytes=b"%PDF synthetic")

    assert result.bank_branch == "1234"
    assert result.account_number is None

