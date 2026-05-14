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
    assert "<FITID>OFXS-" in statement

    parsed = parse_ofx_transactions(statement.encode("utf-8"))
    assert parsed == transactions


def test_build_ofx_statement_accepts_credit_card_account_type() -> None:
    transactions = [
        NormalizedTransaction(
            date="2026-04-01",
            description="ASSINATURA",
            amount=-39.9,
            type="outflow",
        )
    ]

    statement = build_ofx_statement(transactions, account_type="credit_card")

    assert "<CREDITCARDMSGSRSV1>" in statement
    assert "<CCSTMTTRNRS>" in statement
    assert "<CCSTMTRS>" in statement
    assert "<BANKMSGSRSV1>" not in statement


def test_build_ofx_statement_generates_stable_fitids() -> None:
    transactions = [
        NormalizedTransaction(
            date="2026-04-01",
            description="PIX RECEBIDO CLIENTE",
            amount=100.0,
            type="inflow",
        ),
        NormalizedTransaction(
            date="2026-04-01",
            description="PIX RECEBIDO CLIENTE",
            amount=100.0,
            type="inflow",
        ),
    ]

    first_statement = build_ofx_statement(transactions)
    second_statement = build_ofx_statement(transactions)

    assert first_statement == second_statement
    fitid_lines = [line.strip() for line in first_statement.splitlines() if "<FITID>" in line]
    assert len(fitid_lines) == 2
    assert fitid_lines[0] != fitid_lines[1]


def test_build_ofx_statement_includes_ledgerbal_when_closing_balance_is_provided() -> None:
    transactions = [
        NormalizedTransaction(
            date="2026-04-10",
            description="PIX RECEBIDO",
            amount=150.0,
            type="inflow",
        )
    ]

    statement = build_ofx_statement(transactions, closing_balance=56276.06)

    assert "<LEDGERBAL>" in statement
    assert "<BALAMT>56276.06" in statement
    assert "<DTASOF>20260410000000[-3:BRT]" in statement


def test_build_ofx_statement_accepts_bank_branch_and_account_number() -> None:
    transactions = [
        NormalizedTransaction(
            date="2026-04-10",
            description="PIX RECEBIDO",
            amount=150.0,
            type="inflow",
        )
    ]

    statement = build_ofx_statement(transactions, bank_branch="1234-5", account_number="67890-1")

    assert "<BANKACCTFROM>" in statement
    assert "<BRANCHID>12345" in statement
    assert "<ACCTID>678901" in statement
