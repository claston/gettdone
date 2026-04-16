from app.application.models import NormalizedTransaction
from app.application.ofx_parser import parse_ofx_transactions
from app.application.ofx_writer import build_ofx_statement


def test_build_ofx_statement_contains_required_tags_and_roundtrips() -> None:
    transactions = [
        NormalizedTransaction(
            date="2026-04-01",
            description="IFOOD SAO PAULO",
            amount=-58.9,
            type="outflow",
        ),
        NormalizedTransaction(
            date="2026-04-02",
            description="SALARIO",
            amount=2500.0,
            type="inflow",
        ),
    ]

    statement = build_ofx_statement(transactions)

    assert statement.startswith("OFXHEADER:100")
    assert statement.count("<STMTTRN>") == 2
    assert "<TRNTYPE>DEBIT" in statement
    assert "<TRNTYPE>CREDIT" in statement
    assert "<DTPOSTED>20260401000000[-3:BRT]" in statement
    assert "<DTPOSTED>20260402000000[-3:BRT]" in statement
    assert "<TRNAMT>-58.90" in statement
    assert "<TRNAMT>2500.00" in statement

    parsed = parse_ofx_transactions(statement.encode("utf-8"))
    assert parsed == transactions
