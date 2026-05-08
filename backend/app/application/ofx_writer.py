from datetime import datetime

from app.application.models import NormalizedTransaction


def build_ofx_statement(
    transactions: list[NormalizedTransaction],
    *,
    account_type: str | None = None,
    account_id: str | None = None,
) -> str:
    normalized_account_type = _normalize_account_type(account_type)
    range_start, range_end = _resolve_date_range(transactions)

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
    ]

    if normalized_account_type == "credit_card":
        lines.extend(
            [
                "  <CREDITCARDMSGSRSV1>",
                "    <CCSTMTTRNRS>",
                "      <CCSTMTRS>",
                "        <CURDEF>BRL",
                "        <CCACCTFROM>",
                f"          <ACCTID>{_escape_ofx_text(account_id or 'CREDITCARD')}",
                "        </CCACCTFROM>",
                "        <BANKTRANLIST>",
                f"          <DTSTART>{range_start}",
                f"          <DTEND>{range_end}",
            ]
        )
    else:
        lines.extend(
            [
                "  <BANKMSGSRSV1>",
                "    <STMTTRNRS>",
                "      <STMTRS>",
                "        <BANKTRANLIST>",
                f"          <DTSTART>{range_start}",
                f"          <DTEND>{range_end}",
            ]
        )

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

    if normalized_account_type == "credit_card":
        lines.extend(
            [
                "        </BANKTRANLIST>",
                "      </CCSTMTRS>",
                "    </CCSTMTTRNRS>",
                "  </CREDITCARDMSGSRSV1>",
                "</OFX>",
            ]
        )
    else:
        lines.extend(
            [
                "        </BANKTRANLIST>",
                "      </STMTRS>",
                "    </STMTTRNRS>",
                "  </BANKMSGSRSV1>",
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


def _normalize_account_type(raw: str | None) -> str:
    value = str(raw or "").strip().lower()
    if value in {"credit_card", "creditcard", "card"}:
        return "credit_card"
    return "bank"


def _resolve_date_range(transactions: list[NormalizedTransaction]) -> tuple[str, str]:
    if not transactions:
        today = datetime.utcnow().strftime("%Y%m%d000000[-3:BRT]")
        return today, today

    sorted_dates = sorted(item.date[:10] for item in transactions)
    start = _format_ofx_date(sorted_dates[0])
    end = _format_ofx_date(sorted_dates[-1])
    return start, end
