from app.application.layout_profiles.registry import DeclarativeLayoutProfile
from app.application.normalization.pdf_amount_tokens import AmountToken
from app.application.normalization.pdf_tabular_rules import (
    extract_document_reference,
    has_declarative_table_header,
    select_tabular_amount_token,
)


def _profile(*, expected_column_order: tuple[str, ...], column_aliases: dict[str, tuple[str, ...]]) -> DeclarativeLayoutProfile:
    return DeclarativeLayoutProfile(
        profile_name="test",
        bank="Test Bank",
        confidence_label="high",
        min_score_hint=0.7,
        required_keywords=(),
        optional_keywords=(),
        negative_keywords=(),
        header_keywords=(),
        expected_column_order=expected_column_order,
        column_aliases=column_aliases,
        source_path="test.yaml",
    )


def test_select_tabular_amount_token_uses_penultimate_when_two_amounts() -> None:
    tokens = [
        AmountToken(value="1,23", start=12, end=16),
        AmountToken(value="9,87", start=18, end=22),
    ]

    selected = select_tabular_amount_token(tokens, layout_profile=None)

    assert selected is not None
    assert selected.token == tokens[0]
    assert selected.balance_token == tokens[1]
    assert selected.role is None
    assert selected.description_end == 12


def test_select_tabular_amount_token_prefers_credit_debit_roles_when_declarative() -> None:
    tokens = [
        AmountToken(value="0,00", start=20, end=24),
        AmountToken(value="12,34", start=28, end=33),
        AmountToken(value="150,00", start=38, end=44),
    ]
    profile = _profile(
        expected_column_order=("date", "description", "credit", "debit", "balance"),
        column_aliases={"credit": ("credito",), "debit": ("debito",), "balance": ("saldo",)},
    )

    selected = select_tabular_amount_token(tokens, layout_profile=profile)

    assert selected is not None
    assert selected.token == tokens[1]
    assert selected.balance_token == tokens[2]
    assert selected.role == "debit"
    assert selected.description_end == 20


def test_select_tabular_amount_token_uses_sign_when_credit_or_debit_column_is_blank() -> None:
    profile = _profile(
        expected_column_order=("date", "description", "document", "credit", "debit", "balance"),
        column_aliases={"credit": ("credito",), "debit": ("debito",), "balance": ("saldo",)},
    )
    credit_tokens = [
        AmountToken(value="2.850,00", start=40, end=48),
        AmountToken(value="7.479,72", start=54, end=62),
    ]
    debit_tokens = [
        AmountToken(value="1.500,00-", start=40, end=49),
        AmountToken(value="4.629,72", start=54, end=62),
    ]

    credit = select_tabular_amount_token(credit_tokens, layout_profile=profile)
    debit = select_tabular_amount_token(debit_tokens, layout_profile=profile)

    assert credit is not None
    assert credit.token == credit_tokens[0]
    assert credit.role == "credit"
    assert credit.balance_token == credit_tokens[1]
    assert debit is not None
    assert debit.token == debit_tokens[0]
    assert debit.role == "debit"
    assert debit.balance_token == debit_tokens[1]


def test_extract_document_reference_requires_document_column() -> None:
    with_document = _profile(expected_column_order=("description", "document", "amount"), column_aliases={})
    without_document = _profile(expected_column_order=("description", "amount"), column_aliases={})

    assert extract_document_reference("PIX RECEBIDO REF-123", layout_profile=with_document) == "REF-123"
    assert extract_document_reference("PIX RECEBIDO REF-123", layout_profile=without_document) is None


def test_has_declarative_table_header_requires_role_alias_matches() -> None:
    profile = _profile(
        expected_column_order=("date", "description", "amount", "document"),
        column_aliases={
            "date": ("data",),
            "description": ("descricao",),
            "amount": ("valor",),
            "document": ("documento",),
        },
    )

    assert has_declarative_table_header(["Data Descricao Valor Documento"], profile) is True
    assert has_declarative_table_header(["Movimentacoes da conta"], profile) is False
