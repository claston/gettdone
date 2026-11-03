"""PR #543: holder labels and merged institutional titles must not pick a wrong bank."""
import pytest

from app.application.bank_identity import resolve_bank_name, resolve_statement_issuer
from tests.test_statement_issuer_evidence import BODY


@pytest.mark.parametrize("label", ["Razão Social", "Associado", "Empresa/CNPJ", "Contratante", "Nome do titular",
    "Nome/Razão Social CPF/CNPJ", "Nome / Razão Social CNPJ / CPF", "Nome do Titular | CPF / CNPJ"])
@pytest.mark.parametrize("separator", [": ", "\n"])
@pytest.mark.parametrize("title", ["Banco Itau\n", ""])
def test_all_holder_labels_preserve_title_or_layout_identity(label, separator, title):
    source = f"{title}{label}{separator}SANTANDER COMERCIO LTDA\n{BODY}"
    assert resolve_bank_name(layout_inference_name="itau_statement_ptbr", extracted_text=source) == "Itau"
    if not title:
        assert resolve_bank_name(layout_inference_name="account_detail_descending_cash_v1", extracted_text=source) is None


@pytest.mark.parametrize("owner", ["Razão Social: SANTANDER COMERCIO LTDA",
    "Nome/Razão Social CPF/CNPJ\nSANTANDER COMERCIO LTDA", "Associado:\nCPF/CNPJ\nSANTANDER COMERCIO LTDA"])
def test_inline_issuer_before_holder_fields_is_preserved(owner):
    assert resolve_bank_name(extracted_text=f"Banco Itau - {owner}\n{BODY}") == "Itau"


@pytest.mark.parametrize("separator", [" / ", " | ", "    ", "\n"])
@pytest.mark.parametrize("footer", [False, True])
def test_all_institutions_in_one_line_participate_in_conflict_detection(separator, footer):
    titles = f"Banco Santander{separator}Banco Bradesco"
    source = f"{BODY}\nSAC {titles}\n0800 000 0000" if footer else f"{titles}\n{BODY}"
    evidence = resolve_statement_issuer(source)
    assert evidence is not None and evidence.bank_name is None
    assert evidence.source == ("conflicting_footer" if footer else "conflicting_header")
    assert resolve_bank_name(layout_inference_name="itau_statement_ptbr", extracted_text=source) is None


@pytest.mark.parametrize("title,expected", [("Banco Santander / Santander", "Santander"),
    ("VIACREDI COOPERATIVA AILOS", "Viacredi"), ("Viacredi\nAilos", "Viacredi"),
    ("Itau / Banco Itau Unibanco S.A.", "Itau")])
def test_aliases_of_the_same_institution_are_not_conflicting_titles(title, expected):
    assert resolve_bank_name(extracted_text=f"{title}\n{BODY}") == expected
