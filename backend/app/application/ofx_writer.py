from datetime import datetime

from app.application.models import NormalizedTransaction


def build_ofx_statement(transactions: list[NormalizedTransaction]) -> str:
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
        "  <BANKMSGSRSV1>",
        "    <STMTTRNRS>",
        "      <STMTRS>",
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
