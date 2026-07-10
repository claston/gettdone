from app.application.layout_profiles.registry import DeclarativeLayoutProfile, LayoutParsingRules
from app.application.normalization.pdf_tabular_profile_rules import (
    is_profile_opening_balance_description,
    resolve_tabular_profile,
    should_ignore_profile_transaction_description,
)


def _profile() -> DeclarativeLayoutProfile:
    return DeclarativeLayoutProfile(
        profile_name="test",
        bank="Test",
        confidence_label="high",
        min_score_hint=0.7,
        required_keywords=(),
        optional_keywords=(),
        negative_keywords=(),
        header_keywords=(),
        expected_column_order=("date", "description", "amount"),
        column_aliases={
            "date": ("data",),
            "description": ("descricao",),
            "amount": ("valor",),
        },
        source_path="test.yaml",
    )


def test_resolve_tabular_profile_returns_profile_when_header_matches() -> None:
    profile = _profile()
    resolved = resolve_tabular_profile(["Data Descricao Valor"], layout_profile=profile)
    assert resolved is profile


def test_resolve_tabular_profile_returns_none_when_header_does_not_match() -> None:
    profile = _profile()
    assert resolve_tabular_profile(["Movimentacoes da conta"], layout_profile=profile) is None


def test_profile_v2_ignores_declared_transaction_row() -> None:
    profile = _profile_with_parsing(
        ignore_rows=("Detalhamento do Extrato",),
    )

    assert should_ignore_profile_transaction_description("Detalhamento do Extrato", profile) is True
    assert should_ignore_profile_transaction_description("PIX RECEBIDO", profile) is False


def test_profile_v1_does_not_activate_documentary_parsing_rules() -> None:
    profile = _profile_with_parsing(
        schema_version=1,
        ignore_rows=("Detalhamento do Extrato",),
    )

    assert should_ignore_profile_transaction_description("Detalhamento do Extrato", profile) is False


def test_profile_v2_recognizes_declared_opening_balance_row() -> None:
    profile = _profile_with_parsing(
        opening_balance_rows=("Saldo da abertura",),
    )

    assert is_profile_opening_balance_description("Saldo da abertura", profile) is True
    assert is_profile_opening_balance_description("PIX RECEBIDO", profile) is False


def _profile_with_parsing(
    *,
    schema_version: int = 2,
    ignore_rows: tuple[str, ...] = (),
    opening_balance_rows: tuple[str, ...] = (),
) -> DeclarativeLayoutProfile:
    base = _profile()
    return DeclarativeLayoutProfile(
        **{
            **base.__dict__,
            "schema_version": schema_version,
            "parsing": LayoutParsingRules(
                ignore_rows=ignore_rows,
                opening_balance_rows=opening_balance_rows,
            ),
        }
    )
