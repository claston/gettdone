import pytest

from app.application.errors import InvalidFileContentError
from app.application.ofx_parser import parse_ofx_transactions


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

