from dataclasses import dataclass

import pytest

from app.application.parsers.pdf.layout_specific.contract import (
    LayoutSpecificParseContext,
    LayoutSpecificParseResult,
)
from app.application.parsers.pdf.layout_specific.registry import (
    DEFAULT_PDF_LAYOUT_PARSER_REGISTRY,
    PdfLayoutParserRegistry,
)


@dataclass
class FakeLayoutParser:
    layout_names: frozenset[str]
    calls: list[tuple[str, LayoutSpecificParseContext]]

    def parse(self, *, layout_name, lines, context):
        _ = lines
        self.calls.append((layout_name, context))
        return LayoutSpecificParseResult(
            rows=[],
            selected_parser="fake",
            selection_reason="fake_dispatch",
        )


def test_layout_parser_registry_dispatches_only_to_matching_parser() -> None:
    first = FakeLayoutParser(layout_names=frozenset({"layout_a"}), calls=[])
    second = FakeLayoutParser(layout_names=frozenset({"layout_b"}), calls=[])
    registry = PdfLayoutParserRegistry(parsers=(first, second))
    context = LayoutSpecificParseContext(reference_month_year=(7, 2026))

    result = registry.parse(layout_name="layout_b", lines=[], context=context)

    assert result is not None
    assert result.selected_parser == "fake"
    assert first.calls == []
    assert second.calls == [("layout_b", context)]
    assert registry.parse(layout_name="unknown", lines=[], context=context) is None


def test_layout_parser_registry_rejects_duplicate_layout_registration() -> None:
    first = FakeLayoutParser(layout_names=frozenset({"layout_a"}), calls=[])
    second = FakeLayoutParser(layout_names=frozenset({"layout_a"}), calls=[])

    with pytest.raises(ValueError, match="layout_a"):
        PdfLayoutParserRegistry(parsers=(first, second))


def test_default_layout_parser_registry_contains_current_specific_families() -> None:
    assert DEFAULT_PDF_LAYOUT_PARSER_REGISTRY.registered_layout_names == frozenset(
        {
            "santander_cartao_credito_detalhamento_fatura_paisagem_v1",
            "santander_statement_ptbr",
            "bradesco_extrato_unificado_pj_poupanca_facil_a4_v1",
            "banco_do_nordeste_extrato_consolidado_v1",
            "banco_do_nordeste_fundos_investimentos_rentabilidade_v1",
            "banrisul_extrato_texto_movimentos_conta_corrente_v1",
            "itau_empresas_extrato_lancamentos_conta_corrente_v1",
            "itau_empresas_extrato_mensal_conta_corrente_aplicacoes_automaticas_v1",
            "itau_empresas_extrato_30_horas_tabela_v1",
            "itau_extrato_historico_lancamentos_orig_valor_saldo_v1",
            "itau_empresas_extrato_completo_cards_v1",
            "itau_empresas_extrato_30_horas_posicao_conta_corrente_v1",
            "itau_empresas_cobranca_movimentacao_detalhada_v1",
            "itau_empresas_consulta_pagamentos_transferencias_pix_v1",
            "itau_extrato_poupanca_entradas_saidas_v1",
            "itau_empresas_transferencias_recebidas_v1",
            "itau_empresas_extrato_completo_tabela_v1",
            "itau_comprovante_transferencia_pix_v1",
            "banco_inter_extrato_conta_corrente_movimentacoes_v1",
            "banco_inter_extrato_conta_corrente_lista_diaria_v1",
            "banco_inter_fatura_cartao_despesas_v1",
            "banco_inter_extrato_conta_corrente_saldo_transacao_v1",
            "banco_inter_extrato_posicao_renda_fixa_v1",
            "cresol_extrato_conta_corrente_legado_sinal_sufixo_v1",
            "cresol_extrato_consolidado_conta_corrente_valor_cd_v1",
            "cresol_extrato_lancamentos_saldo_dia_pix_credito_v1",
            "cresol_extrato_conta_corrente_moderno_pix_v1",
            "cresol_extrato_rdc_renda_fixa_v1",
            "cresol_ofx_simples_posicao_consolidada_v1",
            "sicoob_fatura_cartao_credito_movimentos_v1",
            "sicoob_sisbr_extrato_conta_corrente_historico_movimentacao_v1",
            "sicoob_sisbr_extrato_conta_corrente_monospace_valor_dc_v1",
            "sicoob_cartao_lancamentos_futuros_v1",
            "sicoob_extrato_aplicacoes_v1",
            "sicoob_extrato_pix_lista_v1",
            "sicoob_extrato_conta_corrente_moderno_v1",
            "sicoob_comprovante_pagamento_boleto_v1",
            "pagbank_extrato_conta_corrente_simples_v1",
            "pagseguro_relatorio_conta_bloqueada_v1",
            "pagbank_extrato_transacoes_operacionais_v1",
            "banrisul_consulta_operacoes_recibos_v1",
            "banrisul_operacoes_pix_v1",
            "banrisul_recibo_pagamento_v1",
            "banrisul_demonstrativo_cdb_automatico_v1",
            "banrisul_fatura_cartao_historico_transacoes_v1",
            "banrisul_extrato_cartao_credito_simples_v1",
            "bradesco_empresas_negocios_extrato_lancamentos_v1",
            "sicredi_extrato_conta_corrente_moderno_movimentacoes_v1",
            "picpay_extrato_conta_grouped_2025_v1",
            "neon_extrato_por_periodo_moderno_v1",
            "banco_bv_extrato_periodo_grouped_v1",
        }
    )
