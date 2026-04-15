from io import BytesIO

from openpyxl import Workbook

from app.application.document_classifier import classify_document


def _build_xlsx_bytes(rows: list[list[object]]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    for row in rows:
        sheet.append(row)
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def test_classify_bank_statement_csv() -> None:
    raw = (
        "date,description,amount\n"
        "2026-04-01,PIX TRANSF RECEBIDA AGENCIA 123 CONTA 456,100.00\n"
        "2026-04-02,TARIFA PACOTE SERVICOS,-12.34\n"
        "2026-04-03,ESTORNO TRANSFERENCIA,-50.00\n"
    ).encode("utf-8")

    result = classify_document("extrato_bancario.csv", raw)

    assert result.semantic_type == "extrato_bancario"
    assert result.confidence >= 0.6
    assert result.evidence


def test_classify_cash_flow_csv() -> None:
    raw = (
        "data,descricao,saldo inicial,saldo final,previsto,realizado\n"
        "2026-04-01,Movimento mensal,1000.00,1200.00,1100.00,1200.00\n"
        "2026-04-02,Movimento mensal,1200.00,900.00,1000.00,900.00\n"
    ).encode("utf-8")

    result = classify_document("fluxo_caixa.csv", raw)

    assert result.semantic_type == "fluxo_caixa"
    assert result.confidence >= 0.5
    assert result.evidence


def test_classify_accounting_sheet_xlsx() -> None:
    raw = _build_xlsx_bytes(
        [
            ["data", "historico", "conta_debito", "conta_credito", "debito", "credito"],
            ["2026-04-01", "Venda consultoria", "1.1.1 Caixa", "3.1.1 Receita", "", 1000.00],
            ["2026-04-02", "Pagamento fornecedor", "2.1.1 Despesa", "1.1.1 Caixa", 250.00, ""],
        ]
    )

    result = classify_document("planilha_contabil_formatada.xlsx", raw)

    assert result.semantic_type == "planilha_contabil_debito_credito"
    assert result.confidence >= 0.5
    assert result.evidence


def test_classify_ofx_payload_with_high_confidence() -> None:
    raw = (
        "OFXHEADER:100\n"
        "DATA:OFXSGML\n"
        "VERSION:102\n"
        "<OFX>\n"
        "<BANKMSGSRSV1>\n"
        "<STMTTRNRS>\n"
        "<STMTRS>\n"
        "<BANKTRANLIST>\n"
        "<STMTTRN><TRNTYPE>DEBIT<DTPOSTED>20231106120000[-3:BRT]<TRNAMT>-120.50<FITID>1<MEMO>TED TRANSFERENCIA FORNECEDOR</STMTTRN>\n"
        "<STMTTRN><TRNTYPE>CREDIT<DTPOSTED>20231107120000[-3:BRT]<TRNAMT>900.00<FITID>2<MEMO>PIX RECEBIDO CLIENTE</STMTTRN>\n"
        "<STMTTRN><TRNTYPE>DEBIT<DTPOSTED>20231108120000[-3:BRT]<TRNAMT>-18.90<FITID>3<MEMO>TARIFA BANCARIA</STMTTRN>\n"
        "</BANKTRANLIST>\n"
        "</STMTRS>\n"
        "</STMTTRNRS>\n"
        "</BANKMSGSRSV1>\n"
        "</OFX>\n"
    ).encode("utf-8")

    result = classify_document("NU_150702837_01NOV2023_30NOV2023.ofx", raw)

    assert result.semantic_type == "extrato_bancario"
    assert result.confidence >= 0.85
    assert result.evidence


def test_operational_sheet_is_not_misclassified_as_bank_statement() -> None:
    raw = (
        "data,descricao,fornecedor,categoria,centro de custo,valor\n"
        "2026-04-01,Pagamento fornecedor mensal,ACME LTDA,Despesa Operacional,Administrativo,-950.00\n"
        "2026-04-02,Pagamento servico infraestrutura,Cloud Corp,Despesa TI,Tecnologia,-420.00\n"
        "2026-04-03,Recebimento cliente contrato,Cliente XPTO,Receita,Comercial,2100.00\n"
        "2026-04-04,Transferencia entre centros de custo,Interno,Reclassificacao,Financeiro,-50.00\n"
    ).encode("utf-8")

    result = classify_document("planilha_operacional_abril.csv", raw)

    assert result.semantic_type != "extrato_bancario"
    assert result.semantic_type in {"contas_a_pagar", "controle_financeiro", "contas_a_receber"}
    assert result.confidence >= 0.5


def test_operational_sheet_with_bank_terms_still_avoids_bank_statement() -> None:
    raw = (
        "data,descricao,fornecedor,categoria,centro de custo,valor\n"
        "2026-04-01,PIX pagamento fornecedor,ACME LTDA,Despesa Operacional,Administrativo,-500.00\n"
        "2026-04-02,TED servico infraestrutura,Cloud Corp,Despesa TI,Tecnologia,-300.00\n"
        "2026-04-03,Estorno ajuste de categoria,Interno,Reclassificacao,Financeiro,80.00\n"
    ).encode("utf-8")

    result = classify_document("sheet_operacional.csv", raw)

    assert result.semantic_type != "extrato_bancario"


def test_classify_operational_sheet_payload_with_consistent_confidence() -> None:
    raw = (
        "data,descricao,fornecedor,categoria,centro de custo,vencimento,valor\n"
        "2026-04-01,Pagamento nota fiscal servico,ACME LTDA,Despesa Operacional,Administrativo,2026-04-10,-950.00\n"
        "2026-04-02,Pagamento fornecedor infraestrutura,Cloud Corp,Despesa TI,Tecnologia,2026-04-12,-420.00\n"
        "2026-04-03,Pagamento boleto aluguel,Imobiliaria XPTO,Despesa Fixa,Operacoes,2026-04-15,-2100.00\n"
        "2026-04-04,Pagamento fornecedor limpeza,Servicos Gerais,Despesa Operacional,Administrativo,2026-04-18,-350.00\n"
    ).encode("utf-8")

    result = classify_document("stress_sheet_detalhado_2026-04.csv", raw)

    assert result.semantic_type != "extrato_bancario"
    assert result.confidence >= 0.55
