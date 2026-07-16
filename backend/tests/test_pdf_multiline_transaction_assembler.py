from app.application import pdf_parser as pdf_parser_module
from app.application.layout_profiles.registry import get_layout_profile
from app.application.parsers.pdf.models import _PdfLine
from app.application.parsers.pdf.multiline_transaction_assembler import parse_multiline_transaction_rows


def _lines(*values: str, page_number: int = 1) -> list[_PdfLine]:
    return [
        _PdfLine(text=value, page_number=page_number, line_number=index)
        for index, value in enumerate(values, start=1)
    ]


def test_multiline_assembler_combines_long_split_transaction_with_document_and_balance() -> None:
    rows, candidates = parse_multiline_transaction_rows(
        _lines(
            "15/07/2026",
            "PIX RECEBIDO",
            "CLIENTE EXEMPLO",
            "DOCUMENTO",
            "123456",
            "CANAL INTERNET",
            "1.250,00 C",
            "8.430,20",
        )
    )

    assert candidates == 1
    assert len(rows) == 1
    assert rows[0].transaction.date == "2026-07-15"
    assert rows[0].transaction.description == "PIX RECEBIDO CLIENTE EXEMPLO CANAL INTERNET"
    assert rows[0].transaction.amount == 1250.0
    assert rows[0].running_balance == 8430.2
    assert rows[0].external_reference_id == "123456"
    assert rows[0].source_page == 1
    assert rows[0].source_line == 1


def test_multiline_assembler_uses_profile_date_and_amount_formats() -> None:
    profile = get_layout_profile("caixa_siatr_saldos_lancamentos_a4_v1")
    assert profile is not None

    rows, candidates = parse_multiline_transaction_rows(
        _lines("150726", "PIX ENVIADO", "654321", "45,90 D"),
        layout_profile=profile,
    )

    assert candidates == 1
    assert len(rows) == 1
    assert rows[0].transaction.date == "2026-07-15"
    assert rows[0].transaction.description == "PIX ENVIADO"
    assert rows[0].transaction.amount == -45.9
    assert rows[0].external_reference_id == "654321"


def test_multiline_assembler_accepts_consistent_unsigned_candidate_cohort() -> None:
    rows, candidates = parse_multiline_transaction_rows(
        _lines(
            "14/07/2026",
            "RENDIMENTO APLICACAO",
            "10,00",
            "15/07/2026",
            "AJUSTE CONTRATUAL",
            "20,00",
        )
    )

    assert candidates == 2
    assert [row.transaction.amount for row in rows] == [10.0, 20.0]


def test_multiline_assembler_rejects_single_weak_or_summary_candidate() -> None:
    weak_rows, weak_candidates = parse_multiline_transaction_rows(
        _lines("15/07/2026", "AJUSTE CONTRATUAL", "20,00")
    )
    summary_rows, summary_candidates = parse_multiline_transaction_rows(
        _lines("15/07/2026", "TOTAL DE ENTRADAS", "1.250,00 C")
    )

    assert weak_candidates == 1
    assert weak_rows == []
    assert summary_candidates == 1
    assert summary_rows == []


def test_multiline_assembler_does_not_join_fields_across_pages() -> None:
    lines = [
        _PdfLine(text="15/07/2026", page_number=1, line_number=1),
        _PdfLine(text="PIX RECEBIDO", page_number=1, line_number=2),
        _PdfLine(text="1.250,00 C", page_number=2, line_number=1),
    ]

    rows, candidates = parse_multiline_transaction_rows(lines)

    assert candidates == 1
    assert rows == []


def test_pdf_parser_uses_multiline_as_last_recovery_and_exposes_metrics(monkeypatch) -> None:
    monkeypatch.setattr(pdf_parser_module, "_parse_grouped_statement_lines", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(pdf_parser_module, "_parse_inline_statement_rows", lambda _lines: ([], 0))
    monkeypatch.setattr(pdf_parser_module, "_parse_tabular_statement_rows", lambda *_args, **_kwargs: ([], 0))
    monkeypatch.setattr(pdf_parser_module, "_parse_columnar_statement_blocks", lambda _lines: ([], 0))
    text = "\n".join(
        [
            "15/07/2026",
            "PIX RECEBIDO",
            "CLIENTE EXEMPLO",
            "123456",
            "1.250,00 C",
            "8.430,20",
        ]
    )

    result = pdf_parser_module._parse_pdf_transactions_from_page_texts([text])

    assert result.parse_metrics["selected_parser"] == "multiline"
    assert result.parse_metrics["multiline_candidates_count"] == 1
    assert result.parse_metrics["multiline_transactions_count"] == 1
    assert result.parse_metrics["multiline_decision"] == "selected_after_existing_parsers_empty"
    assert result.parse_metrics["canonical_source_parser_multiline_count"] == 1
    assert result.canonical_transactions[0].source_parser == "multiline"
