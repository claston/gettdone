from datetime import datetime

from app.application.models import NormalizedTransaction
from app.application.ofx_identity import build_fit_id_sequence


def build_ofx_statement(
    transactions: list[NormalizedTransaction],
    *,
    account_type: str | None = None,
    closing_balance: float | None = None,
    bank_branch: str | None = None,
    account_number: str | None = None,
    bank_id: str | None = None,
) -> str:
    normalized_account_type = str(account_type or "").strip().lower()
    is_credit_card_statement = normalized_account_type in {"credit_card", "credit-card", "card", "cc"}
    if is_credit_card_statement:
        message_open_lines = [
            "  <CREDITCARDMSGSRSV1>",
            "    <CCSTMTTRNRS>",
            "      <CCSTMTRS>",
        ]
        message_close_lines = [
            "      </CCSTMTRS>",
            "    </CCSTMTTRNRS>",
            "  </CREDITCARDMSGSRSV1>",
        ]
    else:
        message_open_lines = [
            "  <BANKMSGSRSV1>",
            "    <STMTTRNRS>",
            "      <STMTRS>",
        ]
        message_close_lines = [
            "      </STMTRS>",
            "    </STMTTRNRS>",
            "  </BANKMSGSRSV1>",
        ]

    normalized_branch = _normalize_numeric_identifier(bank_branch, fallback="0001")
    normalized_account = _normalize_numeric_identifier(account_number, fallback="000000")
    normalized_bank_id = _normalize_numeric_identifier(bank_id, fallback="000")

    lines = [
        "OFXHEADER:100",
        "DATA:OFXSGML",
        "VERSION:102",
        "SECURITY:NONE",
        "ENCODING:USASCII",
        "CHARSET:1252",
        "COMPRESSION:NONE",
        "OLDFILEUID:NONE",
        "NEWFILEUID:NONE",
        "",
        "<OFX>",
        *message_open_lines,
        *(
            [
                "        <CCACCTFROM>",
                f"          <ACCTID>{normalized_account}",
                "        </CCACCTFROM>",
            ]
            if is_credit_card_statement
            else [
                "        <BANKACCTFROM>",
                f"          <BANKID>{normalized_bank_id}",
                f"          <BRANCHID>{normalized_branch}",
                f"          <ACCTID>{normalized_account}",
                "          <ACCTTYPE>CHECKING",
                "        </BANKACCTFROM>",
            ]
        ),
        "        <BANKTRANLIST>",
    ]

    fit_ids = build_fit_id_sequence(transactions)
    for transaction, fit_id in zip(transactions, fit_ids, strict=True):
        lines.extend(
            [
                "          <STMTTRN>",
                f"            <TRNTYPE>{_transaction_type_tag(transaction.type)}",
                f"            <DTPOSTED>{_format_ofx_date(transaction.date)}",
                f"            <TRNAMT>{transaction.amount:.2f}",
                f"            <FITID>{fit_id}",
                f"            <NAME>{_escape_ofx_text(transaction.description)}",
                f"            <MEMO>{_escape_ofx_text(transaction.description)}",
                "          </STMTTRN>",
            ]
        )

    lines.extend(
        [
            "        </BANKTRANLIST>",
            *message_close_lines,
            "</OFX>",
        ]
    )
    if closing_balance is not None:
        ledger_lines = [
            "        <LEDGERBAL>",
            f"          <BALAMT>{float(closing_balance):.2f}",
            f"          <DTASOF>{_resolve_ledger_asof(transactions)}",
            "        </LEDGERBAL>",
        ]
        insert_index = lines.index("        </BANKTRANLIST>") + 1
        for offset, item in enumerate(ledger_lines):
            lines.insert(insert_index + offset, item)
    return "\n".join(lines) + "\n"


def _format_ofx_date(raw_date: str) -> str:
    parsed_date = datetime.strptime(raw_date[:10], "%Y-%m-%d")
    return parsed_date.strftime("%Y%m%d000000[-3:BRT]")


def _transaction_type_tag(raw_type: str) -> str:
    value = str(raw_type).strip().lower()
    if value == "inflow":
        return "CREDIT"
    return "DEBIT"


def _escape_ofx_text(raw_text: str) -> str:
    return (
        str(raw_text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .strip()
    )


def _resolve_ledger_asof(transactions: list[NormalizedTransaction]) -> str:
    if transactions:
        return _format_ofx_date(transactions[-1].date)
    return datetime.now().strftime("%Y%m%d000000[-3:BRT]")


def _normalize_numeric_identifier(value: str | None, *, fallback: str) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return digits or fallback
