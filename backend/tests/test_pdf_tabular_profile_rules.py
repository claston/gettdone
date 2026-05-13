from app.application.layout_profiles.registry import DeclarativeLayoutProfile
from app.application.normalization.pdf_tabular_profile_rules import resolve_tabular_profile


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
