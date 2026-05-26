import pytest

from app.application.errors import InvalidFileContentError
from app.application.normalization.amount import parse_amount


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("-58.90", -58.90),
        ("1.350,00", 1350.00),
        ("R$ 1.350,00", 1350.00),
        ("(120,50)", -120.50),
        ("-R$ 10,00", -10.00),
        ("R$ -10,00", -10.00),
        ("10,00-", -10.00),
        ("10.00-", -10.00),
        ("\u2212R$ 240,24", -240.24),
        ("-53,02\u00b0", -53.02),
        ("-84,043,32", -84043.32),
        ("1.234.567", 1234567.0),
    ],
)
def test_parse_amount_accepts_supported_money_formats(raw: str, expected: float) -> None:
    assert parse_amount(raw) == expected


def test_parse_amount_rejects_invalid_value() -> None:
    with pytest.raises(InvalidFileContentError, match="Invalid amount value"):
        parse_amount("not-money")
