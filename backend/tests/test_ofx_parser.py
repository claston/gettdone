import pytest

from app.application.errors import InvalidFileContentError
from app.application.ofx_parser import parse_ofx_transactions
from app.application.parsers.ofx import parse_ofx_account_metadata


@pytest.mark.parametrize("closing_tags", [False, True])
def test_ofx_bank_metadata_supports_sgml_and_xml(closing_tags: bool) -> None:
    def tag(name: str, value: str) -> str:
        return f"<{name}>{value}" + (f"</{name}>" if closing_tags else "") + "\n"

    raw = ("<OFX><BANKACCTFROM>\n" + tag("BANKID", "33") + tag("BRANCHID", "0012")
           + tag("ACCTID", "000345-6") + "</BANKACCTFROM></OFX>").encode()

    account = parse_ofx_account_metadata(raw)

    assert account.bank_code == "033"
    assert account.bank_name == "Santander"
    assert account.bank_branch == "0012"
    assert account.account_number == "000345-6"


def test_ofx_bank_metadata_does_not_infer_bank_from_transaction_content() -> None:
    account = parse_ofx_account_metadata(b"<OFX><STMTTRN><MEMO>Santander <BANKID>033\n</STMTTRN></OFX>")
    assert account.bank_name is None
    assert account.bank_code is None


def test_ofx_bank_metadata_preserves_unknown_code_without_guessing_a_name() -> None:
    account = parse_ofx_account_metadata(b"<OFX><BANKACCTFROM><BANKID>999\n</BANKACCTFROM></OFX>")
    assert account.bank_code == "999"
    assert account.bank_name is None


def test_ofx_bank_metadata_does_not_label_combined_accounts_as_a_single_bank() -> None:
    account = parse_ofx_account_metadata(
        b"<OFX><BANKACCTFROM><BANKID>041\n</BANKACCTFROM>"
        b"<BANKACCTFROM><BANKID>033\n</BANKACCTFROM></OFX>"
    )
    assert account.bank_code is None
    assert account.bank_name is None


def test_parse_ofx_transactions_happy_path() -> None:
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
            <MEMO>IFOOD SAO PAULO
            <TRNTYPE>DEBIT
          </STMTTRN>
          <STMTTRN>
            <DTPOSTED>20260402123000[-3:BRT]
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

    rows = parse_ofx_transactions(raw)

    assert len(rows) == 2
    assert rows[0].date == "2026-04-01"
    assert rows[0].description == "IFOOD SAO PAULO"
    assert rows[0].amount == -58.90
    assert rows[0].type == "outflow"
    assert rows[1].description == "SALARIO"
    assert rows[1].type == "inflow"


def test_parse_ofx_transactions_raises_for_missing_required_fields() -> None:
    raw = b"<OFX><BANKTRANLIST><STMTTRN><DTPOSTED>20260401</DTPOSTED></STMTTRN></BANKTRANLIST></OFX>"

    with pytest.raises(InvalidFileContentError):
        parse_ofx_transactions(raw)

