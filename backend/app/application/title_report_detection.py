"""Report identity and column vocabulary, independent of ERP/vendor/layout."""
import re

from app.application.normalization.text import normalize_upper_text as upper

PAYABLES = "accounts_payable_report"
RECEIVABLES = "accounts_receivable_report"
TITLE_REPORT_TYPES = {PAYABLES, RECEIVABLES}

COLUMN_ALIASES = {
    "due": ("Data de vencimento", "Data vencimento", "Dt. Vencimento", "Dt. Venc.", "Dt Venc", "Vencimento", "Vencto.", "Vcto."),
    "payment": ("Data de pagamento", "Data pagamento", "Dt. Pag.", "Dt Pag", "Pagamento", "Data recebimento", "Recebimento"),
    "issue": ("Data de emissão", "Data emissão", "Dt. Emissão", "Emissão"),
    "movement": ("Data movimento", "Dt. Mov.", "Dt Mov", "Data"),
    "reference": ("Número documento", "Número do título", "Nº documento", "Nº título", "Seu número",
                  "Documento", "Número", "Título", "Doc."),
    "description": ("Descrição", "Histórico", "Discriminação"),
    "party": ("Cliente/Fornecedor", "Nome/Razão social", "Fornecedor", "Cliente", "Pagador", "Favorecido", "Sacado", "Credor", "Devedor"),
    "original": ("Valor do título", "Valor título", "Valor do documento", "Valor original", "Valor total", "Título(R$)", "Valor"),
    "outstanding": ("Saldo a pagar", "Saldo a receber", "Valor em aberto", "Em aberto", "Saldo aberto", "Saldo devedor", "Saldo"),
    "settled": ("Valor recebido", "Valor pago", "Valor baixado", "Valor liquidado"),
    "discount": ("Descontos", "Desconto"),
    "interest": ("Juros",),
    "fine": ("Multa",),
    "status": ("Situação", "Status", "Estado"),
    "category": ("Categoria", "Centro de custo", "Conta contábil"),
    "tax_id": ("CPF/CNPJ", "CNPJ/CPF", "CNPJ", "CPF"),
}


def identify_title_report(text):
    lines = [upper(line) for line in text.splitlines() if line.strip()]
    for line in lines[:12]:
        if line.startswith("EXTRATO"):
            return None
        match = re.match(r"^(?:(?:RELATORIO|RELACAO|LISTAGEM|CONTROLE) (?:DE )?)?(?:CONTAS|NOTAS|TITULOS) A (PAGAR|RECEBER)\b", line)
        if match:
            return PAYABLES if match[1] == "PAGAR" else RECEIVABLES
    return None


def normalized_label(text):
    return re.sub(r"[^A-Z0-9]+", " ", upper(text).replace("R$", " ")).strip()


def column_role(label):
    value = normalized_label(label)
    if "R$" in upper(label) and value == "TITULO":
        return "original"
    return next((role for role, aliases in COLUMN_ALIASES.items()
                 if value in {normalized_label(alias) for alias in aliases}), None)


def title_report_confidence(text):
    normalized = upper(text)
    return .99 if (re.search(r"VENC|VCTO|EMISSAO", normalized)
                   and re.search(r"VALOR|TITULO\s*\(R\$\)", normalized)
                   and re.search(r"FORNECEDOR|CLIENTE|PAGADOR|DESCRICAO|DOCUMENTO", normalized)) else .8
