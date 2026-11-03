from __future__ import annotations

import re
from dataclasses import dataclass

from app.application.bank_catalog import load_bank_catalog
from app.application.layout_profiles.registry import get_layout_profile
from app.application.normalization.text import normalize_upper_text
from app.application.pdf_document_segmentation import _statement_header

_GENERIC_LAYOUT_NAMES = {"generic_statement_ptbr"}
VERIFIED_STATEMENT_BANKS = {
    "bradesco_unified_cash_flow_report_events_v1": "Bradesco",
    "bb_savings_native_value_columns_v1": "Banco do Brasil",
    "sicredi_visual_signed_cash_v1": "Sicredi",
    "caixa_enriched_printed_cash_columns_v1": "Caixa Economica Federal",
    "pagme_printed_cash_columns_v1": "Pagme",
    "santander_app_printed_cash_columns_v1": "Santander",
    "sicoob_modern_printed_cash_columns_v1": "Sicoob",
    "credisis_native_signed_cash_v1": "CrediSIS",
    "itau_payment_beneficiary_columns_v1": "Itau",
    "dated_native_cash_cards_v1": "Itau",
    "banestes_conta_rende_split_date_v1": "Banestes",
    "bradesco_net_empresa_explicit_no_activity_v1": "Bradesco",
    "pagbank_native_daily_cash_v1": "PagBank",
    "sicoob_sisbr_native_split_cd_v1": "Sicoob",
    "caixa_pessoal_sem_movimentacao_v1": "Caixa Economica Federal",
    "nubank_fatura_cartao_credito_v1": "Nubank",
    "uniprime_conta_corrente_ocr_v1": "Uniprime",
    "sicredi_signed_balance_columns_ocr_v1": "Sicredi",
    "caixa_internet_independent_periods_ocr_v1": "Caixa Economica Federal",
    "bb_english_bank_statement_v1": "Banco do Brasil",
    "itau_empresas_cash_combined_positions_v1": "Itau",
    "brb_monthly_displaced_history_v1": "BRB",
    "credisis_signed_cash_controls_v1": "CrediSIS",
    "banco_do_brasil_autoatendimento_cd_ocr_v1": "Banco do Brasil",
    "sicoob_sisbr_descending_cd_ocr_v1": "Sicoob",
    "banco_do_brasil_sisbb_movim_origem_cd_v1": "Banco do Brasil",
    "sicredi_native_signed_value_balance_v1": "Sicredi",
    "tribanco_online_conta_vinculada_v1": "Tribanco",
    "mercado_pago_extrato_periodo_al_v1": "Mercado Pago",
    "caixa_web_data_efetiva_signed_v1": "Caixa Economica Federal",
    "infinitepay_movimentacoes_data_hora_ocr_v1": "InfinitePay",
    "pagbank_extrato_financeiro_disponivel_ocr_v1": "PagBank",
    "recovery_alelo_prepaid_statement_v1": "Alelo",
    "sumup_extrato_periodo_data_hora_v1": "SumUp Bank",
    "cresol_posicao_consolidada_sem_movimento_v1": "Cresol",
    "bradesco_extrato_mensal_dia_historico_valor_ocr_v1": "Bradesco",
    "bb_excel_horizontal_pages_v1": "Banco do Brasil",
    "banco_amazonia_native_icon_signs_v1": "Banco da Amazônia",
    "recovery_inter_printed_values_v1": "Banco Inter",
    "recovery_cresol_descending_daily_v1": "Cresol",
    "recovery_cresol_colmeia_daily_v1": "Cresol",
    "recovery_btg_investment_cash_v1": "BTG Pactual",
    "recovery_btg_card_invoice_v1": "BTG Pactual",
    "banco_do_brasil_extrato_conta_corrente_lancamentos_v1": "Banco do Brasil",
    "bradesco_totalbank_signed_daily_v1": "Bradesco",
    "bb_cash_rende_facil_combined_v1": "Banco do Brasil",
    "bradesco_extrato_unificado_conta_corrente_native_v1": "Bradesco",
    "bradesco_extrato_movimentacao_periodo_native_v1": "Bradesco",
    "caixa_fatura_cartao_visa_ocr_v1": "Caixa Economica Federal",
    "stone_credit_debit_ocr_v1": "Stone",
    "bradesco_ocr_columns_validated_v1": "Bradesco",
    "banrisul_portal_ocr_v1": "Banrisul",
    "banrisul_portal_monthly_v1": "Banrisul",
    "santander_mister_contador_signed_report_v1": "Santander",
    "xp_digital_descending_v1": "Banco XP",
    "banrisul_extrato_op_doc_sis_v1": "Banrisul",
    "itau_receipt_ocr_daily_controls_v1": "Itau",
    "caixa_ocr_columns_validated_v1": "Caixa Economica Federal",
    "sicredi_ocr_columns_partial_v1": "Sicredi",
    "viacredi_av_extrato_especial_signed_v1": "Viacredi",
    "banco_do_brasil_zero_date_daily_controls_v1": "Banco do Brasil",
    "unicred_fatura_cartao_tracked_native_v1": "Unicred",
    "caixa_gerenciador_periodo_financial_controls_ocr_v1": "Caixa Economica Federal",
}
_LEGACY_LAYOUT_BANK_NAMES = {
    **VERIFIED_STATEMENT_BANKS,
    "ailos_extrato_credito_debito_saldo_v1": "Civia",
    "bb_statement_ptbr": "Banco do Brasil",
    "bradesco_statement_ptbr": "Bradesco",
    "caixa_statement_ptbr": "Caixa Economica Federal",
    "inter_statement_ptbr": "Banco Inter",
    "itau_statement_ptbr": "Itau",
    "nubank_statement_ptbr": "Nubank",
    "santander_statement_ptbr": "Santander",
    "sicredi_statement_ptbr": "Sicredi",
    "sicredi_extrato_html_totais_diarios_v1": "Sicredi",
}
_TEXT_BANK_HINTS = (
    ("BANCO DO BRASIL", "Banco do Brasil"),
    ("CAIXA ECONOMICA FEDERAL", "Caixa Economica Federal"),
    ("BANCO SANTANDER", "Santander"),
    ("SANTANDER", "Santander"),
    ("BRADESCO", "Bradesco"),
    ("ITAU EMPRESAS", "Itau"),
    ("ITAU", "Itau"),
    ("NUBANK", "Nubank"),
    ("BANCO INTER", "Banco Inter"),
    ("INTER", "Banco Inter"),
    ("SICREDI", "Sicredi"),
    ("VIACREDI", "Viacredi"),
    ("BTGPACTUAL", "BTG Pactual"),
    ("WISE", "Wise"),
)
_BRADESCO_TRANSACTION_ANCHOR = "RENTAB.INVEST FACILCRED"
_BRADESCO_TRANSACTION_SUPPORTING_HINTS = (
    "TED-TRANSF ELET DISPON",
    "PIX QR CODE DINAMIC",
    "PAGTO ELETRON COBRANCA",
    "TARIFA BANCARIA LIQUIDACAO QRCODE PIX",
    "CIELO VDA DEBITO MASTER",
)
_UNLISTED_INSTITUTION_MARKERS = (
    "BANCO",
    "COOPERATIVA DE CREDITO",
    "COOPERATIVA DE CRÉDITO",
    "BANCO COOPERATIVO",
    "INSTITUICAO DE PAGAMENTO",
    "INSTITUIÇÃO DE PAGAMENTO",
    "SOCIEDADE DE CREDITO",
    "SOCIEDADE DE CRÉDITO",
    "FINANCEIRA",
)
_PRIVATE_HEADER_MARKERS = (
    "CLIENTE",
    "TITULAR",
    "NOME",
    "CPF",
    "CNPJ",
    "AGENCIA",
    "AGÊNCIA",
    "CONTA",
    "DOCUMENTO",
)
_GENERIC_INSTITUTION_WORDS = frozenset(
    {
        "A",
        "BANCO",
        "COOPERATIVA",
        "COOPERATIVO",
        "CREDITO",
        "DA",
        "DAS",
        "DE",
        "DO",
        "DOS",
        "FINANCEIRA",
        "INSTITUICAO",
        "LTDA",
        "PAGAMENTO",
        "S",
        "SA",
        "SOCIEDADE",
    }
)


def resolve_bank_name(
    *,
    layout_inference_name: str | None = None,
    extracted_text: str | None = None,
) -> str | None:
    issuer = resolve_statement_issuer(extracted_text)
    if issuer is not None:
        return issuer.bank_name
    layout_name = str(layout_inference_name or "").strip()
    if layout_name in {"billing_excel_horizontal_pages_v1", "conta_azul_relatorio_extrato_valor_saldo_v1",
                       "account_detail_descending_cash_v1", "dated_cd_daily_balance_fragment_ocr_v1",
                       "descending_web_cards_daily_cash_v1",
                       "omie_cash_flow_report_events_v1",
                       "dated_cd_three_column_statement_v1"}:
        # Counterparty names in this ledger do not identify its issuer.
        return None
    if layout_name:
        profile = get_layout_profile(layout_name)
        if profile is not None and profile.bank:
            return profile.bank
        legacy_bank = _LEGACY_LAYOUT_BANK_NAMES.get(layout_name.lower())
        if legacy_bank:
            return legacy_bank

    return _match_bank_transaction_fingerprint(normalize_upper_text(extracted_text or ""))


def resolve_conversion_model_label(
    *,
    layout_inference_name: str | None = None,
    bank_name: str | None = None,
) -> str:
    layout_name = str(layout_inference_name or "").strip()
    normalized_layout = layout_name.lower()
    if layout_name and normalized_layout not in _GENERIC_LAYOUT_NAMES:
        return layout_name

    clean_bank_name = str(bank_name or "").strip()
    if clean_bank_name:
        return f"Nao identificado - {clean_bank_name}"
    return "Nao identificado"


@dataclass(frozen=True)
class StatementIssuerEvidence:
    bank_name: str | None
    source: str


def _issuer_result(names: set[str], source: str) -> StatementIssuerEvidence | None:
    if names:
        return StatementIssuerEvidence(next(iter(names)) if len(names) == 1 else None,
                                       source if len(names) == 1 else f"conflicting_{source}")
    return None


def resolve_statement_issuer(extracted_text: str | None) -> StatementIssuerEvidence | None:
    """Use document-owner evidence before layout hints; transactions are not issuer evidence."""
    text = str(extracted_text or "")
    lines = [normalize_upper_text(line) for line in text.splitlines() if line.strip()]
    header = _statement_header(text)
    names = set()
    skip_owner_value = False
    for line in lines[:40]:
        if line not in header:
            break
        if re.match(r"^(?:PIX\b|TED\b|DOC\b|PAGAMENTO\b|TRANSFERENCIA\b|"
                    r"\d{1,2}\s+(?:DE\s+)?(?:JANEIRO|FEVEREIRO|MARCO|ABRIL|MAIO|JUNHO|JULHO|"
                    r"AGOSTO|SETEMBRO|OUTUBRO|NOVEMBRO|DEZEMBRO)\b)", line):
            break
        if skip_owner_value:
            skip_owner_value = False
            continue
        private = re.match(r"^(?:CLIENTE|TITULAR|NOME|CPF|CNPJ|AGENCIA|CONTA|DOCUMENTO)\b", line)
        if private:
            skip_owner_value = not line[private.end():].strip(" :-")
            continue
        # Native extraction may put the issuer and the customer's name on one
        # line. Only the text before the customer/account labels is institutional.
        line = re.split(r"\b(?:CLIENTE|TITULAR|NOME|CPF|CNPJ|AGENCIA)\b", line, maxsplit=1)[0]
        candidate = _institution_candidate_from_line(line)
        bank = _match_known_bank_name(line) or candidate
        if bank:
            names.add(bank)
    evidence = _issuer_result(names, "header")
    if evidence is not None:
        return evidence

    # Contact sections have explicit boundaries and phone/domain evidence. An
    # institution mentioned in a payment, even with a URL, cannot start a footer.
    names = set()
    for index, line in enumerate(lines):
        if re.fullmatch(r"(?:HTTPS?://)?(?:WWW\.)?BB\.COM\.BR\s*-\s*CENTRAL DE ATENDIMENTO BB", line):
            names.add("Banco do Brasil")
            continue
        if not re.match(r"^(?:SAC\b|OUVIDORIA\b|TELEFONES DE CONTATO\b|CENTRAL DE ATENDIMENTO\b|"
                        r"FALE CONOSCO\b|ATENDIMENTO POR E-MAIL\b)", line):
            continue
        block = []
        for contact in lines[index:index + 16]:
            if re.search(r"\d[\d.]*,\d{2}", contact) or re.match(r"(?:\d{1,2}/\d{1,2}(?:/\d{2,4})?|\d{4}-\d{2}-\d{2})\b", contact):
                break
            if _is_transaction_table_header(contact):
                break
            block.append(contact)
        joined = " ".join(block)
        if not re.search(r"\b(?:0800|0300|[34]00\d)[\s-]*\d|[A-Z0-9.-]+\.(?:COM|COOP)\.BR\b", joined):
            continue
        names.update(bank for contact in block if (bank := _match_known_bank_name(contact)))
    return _issuer_result(names, "footer")


def _match_bank_transaction_fingerprint(normalized_text: str) -> str | None:
    if _BRADESCO_TRANSACTION_ANCHOR not in normalized_text:
        return None
    supporting_hits = sum(hint in normalized_text for hint in _BRADESCO_TRANSACTION_SUPPORTING_HINTS)
    return "Bradesco" if supporting_hits >= 2 else None


def _match_known_bank_name(normalized_text: str) -> str | None:

    best_name = ""
    best_score = -1
    for token, bank_name in _TEXT_BANK_HINTS:
        if _contains_token(normalized_text, token):
            score = len(token) + 100
            if score > best_score:
                best_name = bank_name
                best_score = score

    for record in load_bank_catalog():
        display_name = record.short_name or record.name
        for candidate in (display_name, record.name, *record.aliases):
            normalized_candidate = normalize_upper_text(candidate)
            if len(normalized_candidate) < 4:
                continue
            if not _contains_token(normalized_text, normalized_candidate):
                continue
            score = len(normalized_candidate)
            if candidate == display_name:
                score += 20
            elif candidate == record.name:
                score += 10
            if score > best_score:
                best_name = display_name
                best_score = score

    return best_name or None


def _institution_candidate_from_line(line: str) -> str | None:
    candidate = line
    candidate = re.split(r"\s+[-|]\s+EXTRATO\b", candidate, maxsplit=1)[0]
    for marker in _PRIVATE_HEADER_MARKERS:
        candidate = re.split(rf"\b{re.escape(normalize_upper_text(marker))}\b", candidate, maxsplit=1)[0]
    candidate = re.sub(r"^EXTRATO(?: BANCARIO)?(?: DA| DE| DO)?[\s:|-]*", "", candidate).strip(" -:|")
    candidate = re.sub(r"[^A-Z0-9 .,&()'/-]+", " ", candidate)
    candidate = re.sub(r"\s+", " ", candidate).strip(" .,:;|-/")
    if not candidate or len(candidate) > 120 or re.search(r"\d{4,}", candidate):
        return None
    if not any(
        re.match(rf"{re.escape(normalize_upper_text(marker))}(?![A-Z0-9])", candidate)
        for marker in _UNLISTED_INSTITUTION_MARKERS
    ):
        return None
    distinctive_words = {
        word
        for word in re.findall(r"[A-Z0-9]+", candidate)
        if word not in _GENERIC_INSTITUTION_WORDS and len(word) >= 2 and re.search(r"[A-Z]", word)
    }
    return candidate if distinctive_words else None


def _is_transaction_table_header(line: str) -> bool:
    return "DATA" in line and any(marker in line for marker in ("HISTORICO", "DESCRICAO", "VALOR", "LANCAMENTO"))


def _contains_token(text: str, token: str) -> bool:
    pattern = rf"(?<![A-Z0-9]){re.escape(token)}(?![A-Z0-9])"
    return re.search(pattern, text) is not None
