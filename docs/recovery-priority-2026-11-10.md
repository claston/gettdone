# Correções prioritárias de extração — 10/11/2026

Este lote corrige três grupos de recusas confirmadas na auditoria de recuperação:
associação de linhas/colunas BB e Itaú, variantes de metadados Wise e saldo
negativo C6. Os oito PDFs privados da amostra passam a produzir 69 movimentos.
Uma leitura independente da evidência original conferiu a multiplicidade de
cada par data/valor, incluindo sinais; os oito resultados coincidem integralmente.
Os originais, dados pessoais e diagnósticos privados não integram o repositório.

| Leitor | Problema | Comportamento corrigido | Amostra |
| --- | --- | --- | ---: |
| BB | Datas de balancete e movimento na mesma linha textual faziam desaparecer a âncora do débito; o valor era associado à abertura. | Reconhece as duas datas impressas e mantém a primeira como data de balancete, com as referências de origem. Aplica-se a PDF nativo e OCR armazenado. | 17 movimentos |
| Itaú | O cabeçalho parcial do resumo no fim da mesma página substituía as posições das colunas financeiras. | Delimita a tabela de movimentos antes de localizar suas colunas; o resumo mantém seu papel de controle separado. | 5 movimentos |
| Wise | O leitor exigia valores antes da data/ID e rejeitava o campo adicional Referência. | Aceita as duas ordens, preserva o ID da transação e sua capitalização, e não usa a referência do cliente como identidade do movimento. Mantém evidência entre páginas. | 34 movimentos em 5 PDFs |
| C6 | O saldo `-R$ 257,30` era rejeitado ou perdia o sinal quando extraído de uma linha com data. | Aceita um único sinal antes ou depois de `R$`, inclusive na linha seguinte; sinais duplicados e dígitos ilegíveis continuam recusados. | 13 movimentos |

Sete PDFs da amostra retornam `safe_to_export`. O C6 continua com
`review_recommended` por divergência de controles diários. Seus totais mensais
de entradas e saídas conferem, mas isso não autoriza eliminar o alerta diário,
alterar datas ou declarar um saldo global validado.

## Limites preservados

- Não há dedução de valores ou sinais pelo saldo, criação de movimentos ou
  remoção de repetições legítimas.
- IDs Wise repetidos, datas inválidas, movimentos incompletos e valores extras
  continuam impedindo uma extração apresentada como completa.
- Referências comerciais iguais não tornam duas transações Wise duplicadas.
- Divergências aritméticas legíveis conservam os movimentos e exigem revisão.
- O documento pode atravessar páginas; as referências físicas permanecem
  associadas aos dados. A validação existente de períodos e cobertura permanece.
- BB sem movimentação, relatórios de gestão e os demais casos inconclusivos da
  auditoria não fazem parte deste lote.

## Validação

As regressões são exclusivamente sintéticas:

- `backend/tests/test_nov10_priority_recovery.py`: datas BB mescladas, cabeçalho
  de resumo Itaú, BRL/USD Wise nas duas ordens, IDs/referências, continuação entre
  páginas, sinais C6 e rejeição de dados ambíguos.
- `backend/tests/test_nov10_priority_recovery_http.py`: servidor HTTP real,
  conversão CSV e dos quatro leitores, persistência/consulta de relatório,
  exportação CSV/Excel/OFX, revisão C6 e respostas negativas. `/analyze` continua
  desativado com HTTP 405; a rota pública vigente é `/convert`.

A etapa vermelha registrou 18 falhas e 14 casos já protegidos. A primeira
validação dos leitores relacionados passou 153 testes. A seleção final das
novas regressões e do HTTP passou 37 testes. A suíte completa pelo runner
`scripts/test_backend.py` passou **4.679 testes**, com 63 ignorados e duas falhas
esperadas, em 419,91 segundos. Os dois avisos são depreciações existentes do
FastAPI. O runner removeu seu diretório temporário ao concluir.

Ruff, lint de texto UTF-8/mojibake e navegação passaram. Os gates contra
`origin/main` (`8432280`) aprovaram 90 cenários de PDF e 170 de
revisão/armazenamento, sem diferenças inesperadas ou alterações de aprovação.

## Produção e rollback

Não há migração, mudança de schema, dependência, contrato de API ou política de
armazenamento. O trabalho foi feito no checkout principal, sem worktree.
Os testes e replays locais não acionam Textract/Bedrock nem reprocessam produção.
Análises anteriormente armazenadas conservam seus resultados; é necessária nova
conversão após a implantação para aplicar os leitores corrigidos.

O principal risco é uma variante não coberta de agrupamento textual. Os testes
negativos mantêm rejeição de valores/datas ambíguos e os gates comparam o
comportamento existente. Rollback: reverter o commit do lote e publicar a versão
anterior; nenhum rollback de banco é necessário.

## Revisão da PR #573

A data exportada pelo leitor BB é a data de balancete, tanto quando balancete e
movimento chegam na mesma célula quanto em células separadas. A segunda data
permanece na evidência original e não substitui a primeira por causa do
agrupamento de texto. A regressão compara diretamente nativo/OCR nas duas
representações de 01/10/2026 e 30/09/2026, incluindo a data enviada no OFX via HTTP.
