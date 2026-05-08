from datetime import datetime

from app.application.models import NormalizedTransaction


def build_ofx_statement(
    transactions: list[NormalizedTransaction],
    *,
    account_type: str | None = None,
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
        "        <BANKTRANLIST>",
    ]

    for index, transaction in enumerate(transactions, start=1):
        lines.extend(
            [
                "          <STMTTRN>",
                f"            <TRNTYPE>{_transaction_type_tag(transaction.type)}",
                f"            <DTPOSTED>{_format_ofx_date(transaction.date)}",
                f"            <TRNAMT>{transaction.amount:.2f}",
                f"            <FITID>{index}",
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
