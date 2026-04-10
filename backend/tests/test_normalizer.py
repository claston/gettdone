from app.application.models import NormalizedTransaction
from app.application.normalizer import normalize_transactions


def test_normalizer_formats_description_and_keeps_iso_date() -> None:
    rows = [
        NormalizedTransaction(
            date="2026-04-01",
            description="  iFood   sao   paulo ",
            amount=-58.9,
            type="outflow",
        )
    ]

    normalized = normalize_transactions(rows)

    assert normalized[0].date == "2026-04-01"
    assert normalized[0].description == "IFOOD"
    assert normalized[0].amount == -58.9
    assert normalized[0].type == "outflow"


def test_normalizer_enforces_sign_using_type_hint() -> None:
    rows = [
        NormalizedTransaction(
            date="2026-04-01",
            description="SALARIO",
            amount=-2500.0,
            type="credito",
        ),
        NormalizedTransaction(
            date="2026-04-02",
            description="TRANSFERENCIA",
            amount=850.0,
            type="debito",
        ),
    ]

    normalized = normalize_transactions(rows)

    assert normalized[0].amount == 2500.0
    assert normalized[0].type == "inflow"
    assert normalized[1].amount == -850.0
    assert normalized[1].type == "outflow"


def test_normalizer_standardizes_known_establishment_names() -> None:
    rows = [
        NormalizedTransaction(
            date="2026-04-05",
            description=" uber   trip sao paulo 9988 ",
            amount=-27.4,
            type="",
        )
    ]

    normalized = normalize_transactions(rows)

    assert normalized[0].description == "UBER"


def test_normalizer_enforces_sign_using_description_when_type_missing() -> None:
    rows = [
        NormalizedTransaction(
            date="2026-04-01",
            description="pix recebido cliente",
            amount=-300.0,
            type="",
        ),
        NormalizedTransaction(
            date="2026-04-02",
            description="pagamento boleto energia",
            amount=420.0,
            type="",
        ),
    ]

    normalized = normalize_transactions(rows)

    assert normalized[0].amount == 300.0
    assert normalized[0].type == "inflow"
    assert normalized[1].amount == -420.0
    assert normalized[1].type == "outflow"

