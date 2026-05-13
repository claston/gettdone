import pytest

from app.application import pdf_parser as pdf_parser_module
from app.application.errors import InvalidFileContentError
from app.application.pdf_parser import parse_pdf_transactions
from tests.fixtures.pdf_golden_samples import (
    GROUPED_INLINE_MULTILINE_SAMPLE,
    UNICODE_MINUS_SINGLE_ROW_SAMPLE,
    VIACREDI_TABULAR_BALANCE_FAIL,
    VIACREDI_TABULAR_BALANCE_OK,
)


def test_parse_pdf_transactions_handles_inline_and_multiline_amount_rows(monkeypatch) -> None:
    monkeypatch.setattr(
        pdf_parser_module, "_extract_pdf_page_texts", lambda raw_bytes: [GROUPED_INLINE_MULTILINE_SAMPLE]
    )

    result = parse_pdf_transactions(b"%PDF synthetic")

    assert len(result.transactions) == 2
    assert len(result.canonical_transactions) == 2
    assert result.parse_metrics["selected_parser"] == "grouped"
    assert result.transactions[0].date == "2026-03-16"
    assert result.transactions[0].amount == -240.24
    assert result.transactions[1].date == "2026-03-25"
    assert result.transactions[1].amount == -241.05
    assert result.canonical_transactions[0].layout_name == result.layout.layout_name
    assert result.canonical_transactions[0].confidence == result.layout.confidence
    assert result.canonical_transactions[0].source_page == 1
    assert result.canonical_transactions[0].source_line == 2
    assert result.canonical_transactions[0].source_parser == "grouped"
    assert result.parse_metrics["canonical_source_parser_grouped_count"] == 2
    assert result.parse_metrics["canonical_source_parser_types"] == "grouped"


def test_parse_pdf_transactions_parses_unicode_minus_with_currency_prefix(monkeypatch) -> None:
    monkeypatch.setattr(pdf_parser_module, "_extract_pdf_page_texts", lambda raw_bytes: [UNICODE_MINUS_SINGLE_ROW_SAMPLE])

    result = parse_pdf_transactions(b"%PDF synthetic")

    assert len(result.transactions) == 1
    assert len(result.canonical_transactions) == 1
    assert result.transactions[0].date == "2026-04-10"
    assert result.transactions[0].amount == -10.0
    assert result.canonical_transactions[0].source_page == 1
    assert result.canonical_transactions[0].source_line == 1
    assert result.canonical_transactions[0].source_parser == "grouped"


def test_parse_pdf_transactions_does_not_run_ocr_fallback(monkeypatch) -> None:
    monkeypatch.setenv("PDF_OCR_ENABLED", "true")
    monkeypatch.setattr(pdf_parser_module, "_read_native_pdf_page_texts", lambda raw_bytes: [])

    with pytest.raises(InvalidFileContentError, match="OCR fallback is disabled"):
        parse_pdf_transactions(b"%PDF synthetic")


def test_parse_pdf_transactions_uses_declarative_credit_debit_columns(monkeypatch) -> None:
    monkeypatch.setattr(pdf_parser_module, "_extract_pdf_page_texts", lambda raw_bytes: [VIACREDI_TABULAR_BALANCE_OK])
    monkeypatch.setattr(
        pdf_parser_module,
        "infer_pdf_layout",
        lambda text: pdf_parser_module.PdfLayoutInference(
            layout_name="viacredi_ailos_extrato_conta_corrente_v1",
            confidence=0.9,
            used_fallback=False,
        ),
    )

    result = parse_pdf_transactions(b"%PDF synthetic")

    assert len(result.transactions) == 2
    assert result.parse_metrics["selected_parser"] == "tabular"
    assert result.layout.layout_name == "viacredi_ailos_extrato_conta_corrente_v1"
    assert result.transactions[0].amount == 1000.0
    assert result.transactions[0].description == "PIX RECEBIDO CLIENTE 123"
    assert result.transactions[1].amount == -12.34
    assert result.transactions[1].description == "TARIFA PACOTE SERVICOS 456"
    assert result.canonical_transactions[0].bank_name == "Viacredi"
    assert result.canonical_transactions[0].layout_name == "viacredi_ailos_extrato_conta_corrente_v1"
    assert result.canonical_transactions[0].source_page == 1
    assert result.canonical_transactions[0].source_line == 3
    assert result.canonical_transactions[0].source_parser == "tabular"
    assert result.canonical_transactions[0].external_reference_id == "123"
    assert result.canonical_transactions[0].running_balance == 1500.0
    assert result.canonical_transactions[1].external_reference_id == "456"
    assert result.canonical_transactions[1].running_balance == 1487.66
    assert result.parse_metrics["balance_consistency_checked"] == 1
    assert result.parse_metrics["balance_consistency_failed"] == 0
    assert result.parse_metrics["canonical_transactions_count"] == 2
    assert result.parse_metrics["canonical_with_running_balance_count"] == 2
    assert result.parse_metrics["canonical_with_external_reference_count"] == 2
    assert result.parse_metrics["canonical_warning_count"] == 0
    assert result.parse_metrics["canonical_balance_warning_count"] == 0
    assert result.parse_metrics["canonical_warning_transactions_count"] == 0
    assert result.parse_metrics["canonical_warning_types_count"] == 0
    assert result.parse_metrics["canonical_warning_types"] == ""
    assert result.parse_metrics["canonical_warning_types_list"] == ""
    assert result.parse_metrics["canonical_running_balance_coverage_rate"] == 1.0
    assert result.parse_metrics["canonical_external_reference_coverage_rate"] == 1.0
    assert result.parse_metrics["canonical_warning_transaction_rate"] == 0.0
    assert result.parse_metrics["canonical_source_parser_grouped_count"] == 0
    assert result.parse_metrics["canonical_source_parser_inline_count"] == 0
    assert result.parse_metrics["canonical_source_parser_tabular_count"] == 2
    assert result.parse_metrics["canonical_source_parser_columnar_count"] == 0
    assert result.parse_metrics["canonical_source_parser_types_count"] == 1
    assert result.parse_metrics["canonical_source_parser_types"] == "tabular"
    assert result.parse_metrics["canonical_source_parser_types_list"] == "tabular"


def test_parse_pdf_transactions_marks_balance_consistency_warning(monkeypatch) -> None:
    monkeypatch.setattr(
        pdf_parser_module, "_extract_pdf_page_texts", lambda raw_bytes: [VIACREDI_TABULAR_BALANCE_FAIL]
    )
    monkeypatch.setattr(
        pdf_parser_module,
        "infer_pdf_layout",
        lambda text: pdf_parser_module.PdfLayoutInference(
            layout_name="viacredi_ailos_extrato_conta_corrente_v1",
            confidence=0.9,
            used_fallback=False,
        ),
    )

    result = parse_pdf_transactions(b"%PDF synthetic")

    assert len(result.canonical_transactions) == 2
    assert result.parse_metrics["balance_consistency_checked"] == 1
    assert result.parse_metrics["balance_consistency_failed"] == 1
    assert "balance_consistency_failed" in result.canonical_transactions[1].warnings
    assert result.parse_metrics["canonical_warning_count"] == 1
    assert result.parse_metrics["canonical_balance_warning_count"] == 1
    assert result.parse_metrics["canonical_warning_transactions_count"] == 1
    assert result.parse_metrics["canonical_warning_types_count"] == 1
    assert result.parse_metrics["canonical_warning_types"] == "balance_consistency_failed"
    assert result.parse_metrics["canonical_warning_types_list"] == "balance_consistency_failed"
    assert result.parse_metrics["canonical_running_balance_coverage_rate"] == 1.0
    assert result.parse_metrics["canonical_external_reference_coverage_rate"] == 1.0
    assert result.parse_metrics["canonical_warning_transaction_rate"] == 0.5
    assert result.parse_metrics["canonical_source_parser_grouped_count"] == 0
    assert result.parse_metrics["canonical_source_parser_inline_count"] == 0
    assert result.parse_metrics["canonical_source_parser_tabular_count"] == 2
    assert result.parse_metrics["canonical_source_parser_columnar_count"] == 0
    assert result.parse_metrics["canonical_source_parser_types_count"] == 1
    assert result.parse_metrics["canonical_source_parser_types"] == "tabular"
    assert result.parse_metrics["canonical_source_parser_types_list"] == "tabular"


def test_parse_pdf_transactions_adjusts_year_rollover_from_december_to_january(monkeypatch) -> None:
    sample_text = "\n".join(
        [
            "EXTRATO PERIODO 20/12/2025 A 10/01/2026",
            "31/12 Compra mercado 10,00",
            "02/01 PIX recebido 20,00",
        ]
    )
    monkeypatch.setattr(pdf_parser_module, "_extract_pdf_page_texts", lambda raw_bytes: [sample_text])

    result = parse_pdf_transactions(b"%PDF synthetic")

    assert result.parse_metrics["selected_parser"] == "inline"
    assert len(result.transactions) == 2
    assert result.transactions[0].date == "2025-12-31"
    assert result.transactions[0].amount == -10.0
    assert result.transactions[1].date == "2026-01-02"
    assert result.transactions[1].amount == 20.0


def test_parse_pdf_transactions_preserves_explicit_negative_amount_despite_credit_hint(monkeypatch) -> None:
    sample_text = "\n".join(
        [
            "12/06 AJUSTE CONTABIL CREDITO MANUAL -R$ 45,00",
            "13/06 ESTORNO TARIFA PACOTE R$ 45,00",
        ]
    )
    monkeypatch.setattr(pdf_parser_module, "_extract_pdf_page_texts", lambda raw_bytes: [sample_text])

    result = parse_pdf_transactions(b"%PDF synthetic")

    assert result.parse_metrics["selected_parser"] == "inline"
    assert len(result.transactions) == 2
    assert result.transactions[0].date == "2026-06-12"
    assert result.transactions[0].amount == -45.0
    assert result.transactions[0].type == "outflow"
    assert result.transactions[1].date == "2026-06-13"
    assert result.transactions[1].amount == 45.0
    assert result.transactions[1].type == "inflow"


def test_parse_columnar_statement_blocks_skips_incomplete_rows_and_parses_valid_block(monkeypatch) -> None:
    lines = [
        pdf_parser_module._PdfLine(text="15/04", page_number=1, line_number=1),
        pdf_parser_module._PdfLine(text="16/04", page_number=1, line_number=2),
        pdf_parser_module._PdfLine(text="Pagamento", page_number=1, line_number=3),
        pdf_parser_module._PdfLine(text="DEBITO", page_number=1, line_number=4),
        pdf_parser_module._PdfLine(text="10,00", page_number=1, line_number=5),
    ]

    monkeypatch.setattr(pdf_parser_module, "next_columnar_block_index", lambda all_texts, current_index: 5)

    parsed_rows, candidates = pdf_parser_module._parse_columnar_statement_blocks(lines)

    assert candidates == 1
    assert len(parsed_rows) == 1
    assert parsed_rows[0].transaction.date == "2026-04-16"
    assert parsed_rows[0].transaction.description == "Pagamento"
    assert parsed_rows[0].transaction.amount == -10.0
    assert parsed_rows[0].source_page == 1
    assert parsed_rows[0].source_line == 2


def test_build_grouped_amount_only_transaction_returns_none_without_description_parts() -> None:
    line = pdf_parser_module._PdfLine(text="10,00", page_number=1, line_number=5)

    parsed_row = pdf_parser_module._build_grouped_amount_only_transaction(
        date="2026-04-16",
        description_parts=[],
        line=line,
        section_hint="debit",
    )

    assert parsed_row is None


def test_build_grouped_amount_only_transaction_builds_transaction_with_description() -> None:
    line = pdf_parser_module._PdfLine(text="10,00", page_number=1, line_number=5)

    parsed_row = pdf_parser_module._build_grouped_amount_only_transaction(
        date="2026-04-16",
        description_parts=["Pagamento PIX"],
        line=line,
        section_hint="debit",
    )

    assert parsed_row is not None
    assert parsed_row.transaction.date == "2026-04-16"
    assert parsed_row.transaction.description == "Pagamento PIX"
    assert parsed_row.transaction.amount == -10.0
    assert parsed_row.source_page == 1
    assert parsed_row.source_line == 5


def test_parse_grouped_date_line_state_resets_parts_and_updates_hint() -> None:
    line = pdf_parser_module._PdfLine(text="16/04 PIX RECEBIDO 10,00", page_number=1, line_number=2)

    next_date, next_section_hint, next_description_parts, inline_transaction = pdf_parser_module._parse_grouped_date_line_state(
        line=line,
        grouped_date="2026-04-16",
        grouped_rest="PIX RECEBIDO 10,00",
    )

    assert next_date == "2026-04-16"
    assert next_section_hint is None
    assert next_description_parts == []
    assert inline_transaction is not None
    assert inline_transaction.transaction.description == "PIX RECEBIDO"
    assert inline_transaction.transaction.amount == 10.0


def test_parse_inline_statement_line_returns_none_for_non_matching_line() -> None:
    line = pdf_parser_module._PdfLine(text="SALDO ANTERIOR 1.234,56", page_number=1, line_number=1)

    parsed_row = pdf_parser_module._parse_inline_statement_line(line=line, inferred_year=2026)

    assert parsed_row is None


def test_parse_inline_statement_line_builds_transaction_for_valid_line() -> None:
    line = pdf_parser_module._PdfLine(text="10/04 PIX RECEBIDO 25,00", page_number=2, line_number=7)

    parsed_row = pdf_parser_module._parse_inline_statement_line(line=line, inferred_year=2026)

    assert parsed_row is not None
    assert parsed_row.transaction.date == "2026-04-10"
    assert parsed_row.transaction.description == "PIX RECEBIDO"
    assert parsed_row.transaction.amount == 25.0
    assert parsed_row.source_page == 2
    assert parsed_row.source_line == 7


def test_parse_inline_statement_line_accepts_trailing_ocr_noise_after_amount() -> None:
    line = pdf_parser_module._PdfLine(text="10/04 PIX RECEBIDO 25,00 |", page_number=2, line_number=8)

    parsed_row = pdf_parser_module._parse_inline_statement_line(line=line, inferred_year=2026)

    assert parsed_row is not None
    assert parsed_row.transaction.date == "2026-04-10"
    assert parsed_row.transaction.description == "PIX RECEBIDO"
    assert parsed_row.transaction.amount == 25.0
    assert parsed_row.source_page == 2
    assert parsed_row.source_line == 8


def test_parse_inline_statement_line_accepts_trailing_ocr_noise_glyph_after_amount() -> None:
    line = pdf_parser_module._PdfLine(text="10/04 PIX RECEBIDO 25,00 I", page_number=2, line_number=9)

    parsed_row = pdf_parser_module._parse_inline_statement_line(line=line, inferred_year=2026)

    assert parsed_row is not None
    assert parsed_row.transaction.date == "2026-04-10"
    assert parsed_row.transaction.description == "PIX RECEBIDO"
    assert parsed_row.transaction.amount == 25.0
    assert parsed_row.source_page == 2
    assert parsed_row.source_line == 9


def test_parse_inline_statement_line_rejects_trailing_non_noise_text_after_amount() -> None:
    line = pdf_parser_module._PdfLine(text="10/04 PIX RECEBIDO 25,00 DOC", page_number=2, line_number=10)

    parsed_row = pdf_parser_module._parse_inline_statement_line(line=line, inferred_year=2026)

    assert parsed_row is None


def test_parse_inline_statement_rows_parses_split_amount_on_next_line() -> None:
    lines = [
        pdf_parser_module._PdfLine(text="03/04 PIX RECEBIDO CLIENTE ACME", page_number=1, line_number=4),
        pdf_parser_module._PdfLine(text="250,00", page_number=1, line_number=5),
    ]

    parsed_rows, candidates = pdf_parser_module._parse_inline_statement_rows(lines)

    assert candidates == 1
    assert len(parsed_rows) == 1
    assert parsed_rows[0].transaction.date == "2026-04-03"
    assert parsed_rows[0].transaction.description == "PIX RECEBIDO CLIENTE ACME"
    assert parsed_rows[0].transaction.amount == 250.0
    assert parsed_rows[0].source_page == 1
    assert parsed_rows[0].source_line == 4


def test_parse_inline_statement_rows_parses_multiline_description_then_amount() -> None:
    lines = [
        pdf_parser_module._PdfLine(text="03/04 PAGAMENTO FORNECEDOR ALFA", page_number=1, line_number=10),
        pdf_parser_module._PdfLine(text="INDUSTRIA E COMERCIO LTDA", page_number=1, line_number=11),
        pdf_parser_module._PdfLine(text="150,25", page_number=1, line_number=12),
    ]

    parsed_rows, candidates = pdf_parser_module._parse_inline_statement_rows(lines)

    assert candidates == 1
    assert len(parsed_rows) == 1
    assert parsed_rows[0].transaction.date == "2026-04-03"
    assert parsed_rows[0].transaction.description == "PAGAMENTO FORNECEDOR ALFA INDUSTRIA E COMERCIO LTDA"
    assert parsed_rows[0].transaction.amount == -150.25
    assert parsed_rows[0].source_page == 1
    assert parsed_rows[0].source_line == 10


def test_parse_inline_statement_rows_ignores_ocr_noise_line_in_pending_multiline() -> None:
    lines = [
        pdf_parser_module._PdfLine(text="03/04 PAGAMENTO FORNECEDOR ALFA", page_number=1, line_number=60),
        pdf_parser_module._PdfLine(text="||", page_number=1, line_number=61),
        pdf_parser_module._PdfLine(text="INDUSTRIA E COMERCIO LTDA", page_number=1, line_number=62),
        pdf_parser_module._PdfLine(text="150,25", page_number=1, line_number=63),
    ]

    parsed_rows, candidates = pdf_parser_module._parse_inline_statement_rows(lines)

    assert candidates == 1
    assert len(parsed_rows) == 1
    assert parsed_rows[0].transaction.date == "2026-04-03"
    assert parsed_rows[0].transaction.description == "PAGAMENTO FORNECEDOR ALFA INDUSTRIA E COMERCIO LTDA"
    assert parsed_rows[0].transaction.amount == -150.25
    assert parsed_rows[0].source_page == 1
    assert parsed_rows[0].source_line == 60


def test_parse_inline_statement_rows_cancels_pending_on_balance_line() -> None:
    lines = [
        pdf_parser_module._PdfLine(text="03/04 PAGAMENTO FORNECEDOR ALFA", page_number=1, line_number=20),
        pdf_parser_module._PdfLine(text="SALDO DO DIA 1.274,16", page_number=1, line_number=21),
        pdf_parser_module._PdfLine(text="150,25", page_number=1, line_number=22),
    ]

    parsed_rows, candidates = pdf_parser_module._parse_inline_statement_rows(lines)

    assert candidates == 0
    assert parsed_rows == []


def test_parse_inline_statement_rows_cancels_pending_on_header_line() -> None:
    lines = [
        pdf_parser_module._PdfLine(text="03/04 PAGAMENTO FORNECEDOR ALFA", page_number=1, line_number=30),
        pdf_parser_module._PdfLine(text="EXTRATO CONTA CORRENTE - CONTINUACAO", page_number=1, line_number=31),
        pdf_parser_module._PdfLine(text="150,25", page_number=1, line_number=32),
    ]

    parsed_rows, candidates = pdf_parser_module._parse_inline_statement_rows(lines)

    assert candidates == 0
    assert parsed_rows == []


def test_parse_inline_statement_rows_cancels_pending_on_page_change_before_amount() -> None:
    lines = [
        pdf_parser_module._PdfLine(text="03/04 PAGAMENTO FORNECEDOR ALFA", page_number=1, line_number=40),
        pdf_parser_module._PdfLine(text="150,25", page_number=2, line_number=1),
    ]

    parsed_rows, candidates = pdf_parser_module._parse_inline_statement_rows(lines)

    assert candidates == 0
    assert parsed_rows == []


def test_parse_inline_statement_rows_cancels_pending_on_tabular_header_before_amount() -> None:
    lines = [
        pdf_parser_module._PdfLine(text="03/04 PAGAMENTO FORNECEDOR ALFA", page_number=1, line_number=50),
        pdf_parser_module._PdfLine(
            text="DATA DESCRICAO DOCUMENTO CREDITO (R$) DEBITO (R$) SALDO (R$)",
            page_number=1,
            line_number=51,
        ),
        pdf_parser_module._PdfLine(text="150,25", page_number=1, line_number=52),
    ]

    parsed_rows, candidates = pdf_parser_module._parse_inline_statement_rows(lines)

    assert candidates == 0
    assert parsed_rows == []


def test_parse_inline_statement_line_accepts_trailing_mixed_ocr_noise_after_amount() -> None:
    line = pdf_parser_module._PdfLine(text="10/04 PIX RECEBIDO 25,00 ||I", page_number=2, line_number=11)

    parsed_row = pdf_parser_module._parse_inline_statement_line(line=line, inferred_year=2026)

    assert parsed_row is not None
    assert parsed_row.transaction.date == "2026-04-10"
    assert parsed_row.transaction.description == "PIX RECEBIDO"
    assert parsed_row.transaction.amount == 25.0
    assert parsed_row.source_page == 2
    assert parsed_row.source_line == 11


def test_parse_tabular_statement_line_returns_none_when_no_date_prefix() -> None:
    line = pdf_parser_module._PdfLine(text="SALDO ANTERIOR 1.000,00", page_number=1, line_number=1)

    parsed_row = pdf_parser_module._parse_tabular_statement_line(
        line=line,
        inferred_year=2026,
        tabular_profile=None,
    )

    assert parsed_row is None


def test_parse_tabular_statement_line_builds_transaction_with_metadata() -> None:
    line = pdf_parser_module._PdfLine(
        text="10/04 PIX RECEBIDO CLIENTE 123 1.000,00 1.500,00",
        page_number=3,
        line_number=12,
    )

    parsed_row = pdf_parser_module._parse_tabular_statement_line(
        line=line,
        inferred_year=2026,
        tabular_profile=None,
    )

    assert parsed_row is not None
    assert parsed_row.transaction.date == "2026-04-10"
    assert parsed_row.transaction.description == "PIX RECEBIDO CLIENTE 123"
    assert parsed_row.transaction.amount == 1000.0
    assert parsed_row.running_balance == 1500.0
    assert parsed_row.source_page == 3
    assert parsed_row.source_line == 12


def test_update_grouped_section_state_keeps_description_when_hint_is_unchanged() -> None:
    next_hint, next_parts, should_continue = pdf_parser_module._update_grouped_section_state(
        normalized_line="COMPRA CARTAO",
        current_section_hint="debit",
        description_parts=["compra mercado"],
    )

    assert next_hint == "debit"
    assert next_parts == ["compra mercado"]
    assert should_continue is False


def test_update_grouped_section_state_resets_description_when_hint_changes() -> None:
    next_hint, next_parts, should_continue = pdf_parser_module._update_grouped_section_state(
        normalized_line="TOTAL DE ENTRADAS",
        current_section_hint="outflow",
        description_parts=["compra mercado"],
    )

    assert next_hint == "inflow"
    assert next_parts == []
    assert should_continue is True


def test_handle_grouped_ignored_line_resets_description_parts() -> None:
    next_parts, should_continue = pdf_parser_module._handle_grouped_ignored_line(
        normalized_line="SALDO INICIAL DO DIA",
        description_parts=["compra mercado"],
    )

    assert next_parts == []
    assert should_continue is True


def test_handle_grouped_ignored_line_keeps_description_when_not_ignored() -> None:
    next_parts, should_continue = pdf_parser_module._handle_grouped_ignored_line(
        normalized_line="COMPRA MERCADO",
        description_parts=["compra mercado"],
    )

    assert next_parts == ["compra mercado"]
    assert should_continue is False


def test_append_grouped_description_part_ignores_blank_text() -> None:
    parts = ["compra mercado"]

    next_parts = pdf_parser_module._append_grouped_description_part(
        description_parts=parts,
        raw_text="   ",
    )

    assert next_parts == ["compra mercado"]


def test_append_grouped_description_part_appends_cleaned_text() -> None:
    parts = ["compra mercado"]

    next_parts = pdf_parser_module._append_grouped_description_part(
        description_parts=parts,
        raw_text="  pagamento pix  ",
    )

    assert next_parts == ["compra mercado", "pagamento pix"]


def test_resolve_next_columnar_index_increments_when_row_is_none() -> None:
    next_index = pdf_parser_module._resolve_next_columnar_index(
        line_texts=["10/04", "Pagamento", "DEBITO", "10,00"],
        current_index=2,
        parsed_row=None,
    )

    assert next_index == 3


def test_resolve_next_columnar_index_uses_block_rule_when_row_exists(monkeypatch) -> None:
    line_texts = ["10/04", "Pagamento", "DEBITO", "10,00"]
    parsed_row = pdf_parser_module._ParsedTransaction(
        transaction=pdf_parser_module.NormalizedTransaction(
            date="2026-04-10",
            description="Pagamento",
            amount=-10.0,
            type="outflow",
        ),
        source_page=1,
        source_line=1,
    )
    monkeypatch.setattr(pdf_parser_module, "next_columnar_block_index", lambda lines, current_index: 99)

    next_index = pdf_parser_module._resolve_next_columnar_index(
        line_texts=line_texts,
        current_index=0,
        parsed_row=parsed_row,
    )

    assert next_index == 99


def test_build_tabular_amount_details_returns_signed_amount_and_balance() -> None:
    details = pdf_parser_module._build_tabular_amount_details(
        amount_token_value="1.000,00",
        selected_role="credit",
        raw_description="PIX RECEBIDO CLIENTE",
        balance_token_value="1.500,00",
    )

    assert details["signed_amount"] == 1000.0
    assert details["running_balance"] == 1500.0


def test_build_tabular_amount_details_handles_missing_balance() -> None:
    details = pdf_parser_module._build_tabular_amount_details(
        amount_token_value="10,00",
        selected_role="debit",
        raw_description="TARIFA PACOTE",
        balance_token_value=None,
    )

    assert details["signed_amount"] == -10.0
    assert details["running_balance"] is None


def test_classify_tabular_statement_line_marks_candidate_without_description() -> None:
    line = pdf_parser_module._PdfLine(text="10/04 1,00", page_number=1, line_number=1)

    parsed_row, is_candidate = pdf_parser_module._classify_tabular_statement_line(
        line=line,
        inferred_year=2026,
        tabular_profile=None,
    )

    assert is_candidate is True
    assert parsed_row is None


def test_handle_grouped_amount_only_line_resets_parts_when_parsed() -> None:
    line = pdf_parser_module._PdfLine(text="10,00", page_number=1, line_number=5)

    parsed_row, next_parts, should_continue = pdf_parser_module._handle_grouped_amount_only_line(
        current_date="2026-04-16",
        description_parts=["Pagamento PIX"],
        line=line,
        section_hint="debit",
    )

    assert should_continue is True
    assert parsed_row is not None
    assert parsed_row.transaction.amount == -10.0
    assert next_parts == []


def test_accumulate_tabular_row_increments_candidates_without_transaction() -> None:
    transactions: list[pdf_parser_module._ParsedTransaction] = []

    next_candidates = pdf_parser_module._accumulate_tabular_row(
        transactions=transactions,
        parsed_row=None,
        is_candidate=True,
        candidates=2,
    )

    assert next_candidates == 3
    assert transactions == []


def test_accumulate_tabular_row_adds_transaction_when_present() -> None:
    transactions: list[pdf_parser_module._ParsedTransaction] = []
    parsed_row = pdf_parser_module._ParsedTransaction(
        transaction=pdf_parser_module.NormalizedTransaction(
            date="2026-04-10",
            description="Pagamento",
            amount=-10.0,
            type="outflow",
        ),
        source_page=1,
        source_line=1,
    )

    next_candidates = pdf_parser_module._accumulate_tabular_row(
        transactions=transactions,
        parsed_row=parsed_row,
        is_candidate=True,
        candidates=0,
    )

    assert next_candidates == 1
    assert len(transactions) == 1
    assert transactions[0].transaction.description == "Pagamento"
