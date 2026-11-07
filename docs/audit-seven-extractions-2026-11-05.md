# Correções de sete PDFs auditados em 05/11/2026

Os sete arquivos revelam cinco famílias de defeitos. São variações de extratos
conhecidos e dois relatórios financeiros que exigem interpretação própria.
Uma conversão concluída com aviso não significa que os movimentos estão corretos.

| Documento | Classificação e causa | Comportamento corrigido |
| --- | --- | --- |
| Safra, 56 páginas | Extrato conhecido; data de emissão prevalecia sobre o período e palavras da descrição invertiam créditos. | Preserva 1.227 movimentos de 2025 e corrige 28 sinais. |
| Safra, 66 páginas | Mesma família, com blocos e períodos independentes. | Preserva 1.422 movimentos e corrige 33 sinais; lacunas continuam exigindo revisão. |
| Bradesco mensal + Últimos Lançamentos | Variação de continuação sem título repetido; perda do leitor de colunas após separar os blocos. | Recupera 42 movimentos: 285 → 327, mantendo a data contextual e a página original. |
| C6 mensal | Mudança de mês; totais do resumo confundidos com movimentos do mês anterior. | 15 → 13 movimentos; totais mensais servem apenas à conferência. |
| Stone OCR | Ordem de leitura coloca descrição depois dos valores; tipo Entrada sobrepunha sinal negativo explícito. | 142 → 147 movimentos; recupera cinco linhas e conserva a tarifa negativa. |
| BB portfólio | Classificação semântica incorreta: posições e rentabilidade tratadas como conta corrente. | Zero movimentos de caixa inventados; posições, rendimentos e indicadores preservados como observações em CSV/Excel. |
| Sicoob cobrança analítica | Colunas de vencimento/pagamento/lançamento e valor nominal/pago interpretadas como extrato bancário. | 89 → 79 cobranças; soma dos valores pagos 21.474,80; três datas preservadas e nenhum saldo inventado. |

Os Safra se sobrepõem: o arquivo maior inclui o menor. Não somar as quantidades
como movimentos ou erros independentes.

## Política de extração

- O período explícito determina o ano; data de impressão não fornece contexto
  ausente. Datas ambíguas não são inventadas.
- Sinal monetário explícito prevalece sobre palavras como `DEBITO` na descrição
  de vendas e sobre `Entrada` no tipo de uma tarifa negativa.
- Resumos, saldos, posições e percentuais mantêm seus papéis financeiros.
- Blocos legíveis mantêm movimentos repetidos legítimos e referências de origem.
  Controles internos não certificam a continuidade global entre períodos.
- Divergências de totais continuam em revisão. Campos financeiros ilegíveis,
  contas incompatíveis ou situação não confirmada não viram extração completa.
- O portfólio e a cobrança analítica utilizam o contrato existente de eventos e
  observações. OFX fica indisponível nesses dois relatórios; CSV/Excel preservam
  suas colunas. Extratos bancários continuam exportáveis como antes.

## Evidência e reprodução

Os originais e as evidências de OCR permanecem em armazenamento local privado,
fora do Git. A comparação dos seis documentos com movimentos usa a multiplicidade
de pares data/valor contra leitura independente, não apenas a contagem ou a soma.
O portfólio é conferido como posições e observações, incluindo bruto menos imposto
igual a líquido. O replay reutiliza OCR capturado; não chama serviços externos.

As regressões públicas usam exclusivamente dados fictícios:

- `backend/tests/test_audited_financial_extractions.py`: período/ano, sinais,
  resumos, continuação, repetição legítima, proveniência, OCR, lacunas e ambiguidade.
- `backend/tests/test_audited_financial_extractions_http.py`: servidor HTTP real,
  conversão, armazenamento, relatório e exportações CSV/Excel/OFX, incluindo
  recusas de OFX para relatórios especializados e relatório inexistente.

Executar com o Python do ambiente virtual do projeto:

```text
python scripts/test_backend.py
python -m ruff check backend
python scripts/lint_frontend_text.py
python scripts/lint_frontend_navigation.py
python scripts/check_pdf_compatibility.py --base-ref 99afe01 --approvals docs/statement-issuer-evidence-approvals.json
python scripts/check_review_compatibility.py --base-ref 99afe01
```

No Windows, executar pytest/runner fora do sandbox de arquivos. O runner administra
seu diretório temporário e produz JUnit e tempos. A rota pública atual é
`POST /convert`; `POST /analyze` já retorna 405 nesta base.

## Limites da auditoria e compatibilidade

Este conjunto corrige os sete erros confirmados. As outras falhas identificadas
na auditoria têm diagnósticos próprios: BB não reproduzido localmente, Inter com
datas realmente ambíguas, BB fundos sem atividade, livro caixa com caracteres
privados no texto nativo e limite operacional de páginas. Elas não são declaradas
resolvidas por estas correções. Revisões sem conferência independente integral
também não são removidas por um replay coincidente.

Não há migração, mudança de esquema persistente ou reprocessamento automático de
análises existentes. Os novos campos usam o contrato financeiro já suportado.
Risco principal: variações ainda não cobertas das tabelas reconhecidas exigirem
revisão. Reverter o commit e publicar a versão anterior restaura os leitores;
relatórios já gerados permanecem armazenados e precisam de nova conversão para
receber correções posteriores. Não há rollback de banco de dados.
