from app.application.normalization.text import normalize_upper_text

INFLOW_HINTS = (
    "TRANSFERENCIA RECEBIDA",
    "RECEBIMENTO",
    "ESTORNO",
    "CREDITO",
    "SALARIO",
)
OUTFLOW_HINTS = (
    "TRANSFERENCIA ENVIADA",
    "PAGAMENTO",
    "COMPRA",
    "DEBITO",
    "SAIDA",
    "TARIFA",
    "SAQUE",
)
IGNORED_LINE_PREFIXES = (
    "SALDO INICIAL",
    "SALDO FINAL",
    "MOVIMENTACOES",
    "EXTRATO GERADO DIA",
    "OUVIDORIA:",
)
IGNORED_LINE_TOKENS = (
    "VALORES EM R",
    "CNPJ AGENCIA CONTA",
)
IGNORED_TRANSACTION_HINTS = (
    "SALDO DO DIA",
    "SALDO DIA",
    "SALDO FINAL",
    "SALDO INICIAL",
    "LIMITE DA CONTA",
    "TOTAL DE ENTRADAS",
    "TOTAL DE SAIDAS",
    "RESUMO DA FATURA",
    "FATURA ANTERIOR",
    "PAGAMENTO RECEBIDO",
    "DADOS DA CONTA ORIGEM",
    "NOME DO TITULAR",
    "NUMERO DA CONTA",
    "TIPO DE CONTA",
    "TIPO DE EXTRATO",
    "TOTAL DISPONIVEL",
    "AGENCIA | CONTA",
    "DATA DA OPERACAO",
    "OPERACAO:",
    "EXTRATO MENSAL / POR PERIODO",
    "BRADESCO NET EMPRESA",
)


def section_hint(text: str) -> str | None:
    normalized = normalize_upper_text(text)
    if "TOTAL DE ENTRADAS" in normalized:
        return "inflow"
    if "TOTAL DE SAIDAS" in normalized:
        return "outflow"
    return None


def should_ignore_line(normalized_line: str) -> bool:
    if not normalized_line:
        return True
    if normalized_line in {"-", "--"}:
        return True
    if any(normalized_line.startswith(prefix) for prefix in IGNORED_LINE_PREFIXES):
        return True
    if any(token in normalized_line for token in IGNORED_LINE_TOKENS):
        return True
    return False


def should_skip_transaction_description(description: str) -> bool:
    normalized_description = normalize_upper_text(description)
    if not normalized_description:
        return True
    if normalized_description.startswith("SALDO ANTERIOR") or normalized_description.startswith("SALDO INICIAL"):
        return False
    if any(hint in normalized_description for hint in IGNORED_TRANSACTION_HINTS):
        return True
    if normalized_description == "SALDO":
        return True
    if normalized_description.startswith("SALDO "):
        return True
    return False


def apply_sign_hints(amount: float, description: str, section_hint_value: str | None) -> float:
    normalized_description = normalize_upper_text(description)
    if any(token in normalized_description for token in INFLOW_HINTS):
        return abs(amount)
    if any(token in normalized_description for token in OUTFLOW_HINTS):
        return -abs(amount)
    if section_hint_value == "inflow":
        return abs(amount)
    if section_hint_value == "outflow":
        return -abs(amount)
    return amount
