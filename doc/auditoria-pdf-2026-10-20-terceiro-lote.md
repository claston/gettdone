# PDF: terceiro lote de 20/10/2026

## Recorte e comparação

A varredura encontrou cinco PDFs inéditos entre as 58 capturas anteriores e
as 63 capturas de 20/10. Foram recebidos das 15h56 às 16h47 de Brasília,
antes do merge da PR #460, às 17h57. A implementação parte de `cd59fb4`.

Os cinco arquivos foram reprocessados em `25219b0`, anterior à #460, e em
`cd59fb4`, posterior ao merge. Os resultados financeiros foram iguais nas
duas versões. Não se identificou regressão da #460 neste lote; essa
comparação não determina quando os problemas surgiram em versões mais antigas.

O Banco do Brasil já fechava corretamente com a correção anterior: 13
movimentos, saldo inicial de R$ 1.323,98 e final de R$ 0,00. Os outros quatro
arquivos exigiram as correções descritas abaixo.

As capturas registram `APP_RELEASE=4d346f3`. Esse campo vem do ambiente e,
isoladamente, não comprova qual código estava implantado. Os PDFs e o OCR
real preservado ficam no cache privado da auditoria; os testes versionados
usam documentos sintéticos, sem dados ou identificadores dos clientes.

## Antes e depois

| Captura | Documento | Antes | Depois |
|---|---|---|---|
| `6e8330cdbf84` | BB, uma página nativa | Já coberto no código atual. | 13 movimentos; saldo final R$ 0,00; nenhuma divergência. |
| `419f63a1dd44` | Bradesco Unificado PJ, quatro páginas | 32 movimentos; saldo final R$ 7.246,48, pertencente ao Invest Fácil. | 49 movimentos; créditos e débitos de R$ 70.528,48; saldo inicial e final da conta corrente R$ 1,00. |
| `46e076cc98b1` | Bradesco Movimentação do Período, oito páginas | 128 movimentos; débitos quase todos positivos; saldo final R$ 118.636,29. | 392 movimentos; créditos e débitos de R$ 692.289,66; saldo inicial e final R$ 1,00. |
| `6658743536ae` | Santander Consolidado Inteligente, 12 páginas | 245 movimentos, dois créditos invertidos e saldo inicial R$ 10 mil retirado de um CDB. | 245 movimentos; saldo inicial zero; créditos R$ 356.343,25; débitos R$ 393.903,00; saldo final −R$ 37.559,75. |
| `c0360dfc06e1` | Fatura Caixa Visa, duas páginas em contornos vetoriais | 67 supostos movimentos, incluindo limite e simulações de financiamento. | 14 movimentos; dívida anterior R$ 1.925,66 e atual R$ 1.653,80; nenhuma divergência financeira; um alerta de data da anuidade. |

Quatro documentos têm texto nativo utilizável, inclusive dois classificados
como mistos por conterem gráficos. A Caixa depende de OCR porque as letras
são contornos vetoriais, sem uma camada de texto extraível. A queda no uso
de OCR não resolve, por si só, erros de classificação, direção das colunas
ou confusão entre saldo bancário, investimentos e dívida de cartão. Este
lote não permite estimar a taxa de erros de todos os uploads da produção.

## Bradesco: identificação e cobertura das colunas

Os dois extratos foram identificados como `caixa_extrato_por_periodo_web_v1`
e seguiram para o leitor genérico. A data aparece uma vez por grupo; há
movimentos sem nova data, descrições continuadas e quebras de página no meio
do dia. Os valores são positivos na impressão: a direção depende da coluna
Crédito ou Débito.

O novo leitor usa as posições nativas dos caracteres, sem renderização nem
OCR. Os cabeçalhos de cada página localizam data, histórico, documento,
crédito, débito e saldo. A data continua válida entre linhas e páginas.
Referências são preservadas como texto, incluindo zeros à esquerda.

O limite do livro de conta corrente é o total impresso, com créditos,
débitos e saldo final. Os saldos intermediários e os dois totais brutos
precisam fechar exatamente, usando valores decimais. Um crédito e um débito
omitidos, mesmo mantendo o saldo líquido, fazem os totais brutos divergirem.
Campos financeiros sem associação, páginas fora de sequência, contas
diferentes e um segundo livro concatenado geram erro de validação.

No Unificado PJ, as posições de Invest Fácil, fundos e CDB são seções
posteriores. R$ 7.246,48 é uma posição de investimento; o fechamento da conta
corrente é R$ 1,00. O leitor de poupança Bradesco já existente, que usa
`Documento` e outra estrutura, mantém seu caminho original. A nova regra
do Unificado exige a coluna `Docto` da variante auditada.

## Santander: direção dos créditos e origem do saldo inicial

Em 04/02, R$ 23,78 aparece como `PAGAMENTO A FORNECEDORES`; em 18/02,
R$ 257,47 aparece como `PAGAMENTO CARTAO DE DEBITO`. Ambos estão na coluna
Créditos. A leitura anterior tratava os dois como saídas. Os erros somam
R$ 281,25 e alteram o líquido em R$ 562,50.

O leitor nativo usa as colunas, mantém as descrições continuadas e confere
cada saldo impresso e os totais do resumo mensal. O saldo inicial é o de
conta corrente em 31/01: zero. A linha `SALDO ANTERIOR` da seção CDB na
página 9 informa um principal de R$ 10 mil e não participa desse saldo.

O livro termina em `SALDO EM 28/02`. Saldos por período, débitos automáticos
apresentados novamente e informações de investimentos posteriores ficam
fora dos movimentos. Os 245 movimentos fecham com os dois totais brutos e
o saldo final negativo. A nova regra é restrita ao formulário
`Extrato_PJ_A4_Inteligente`; os demais leitores Santander permanecem disponíveis.

## Caixa: interpretar a fatura depois do OCR

O OCR real já continha os valores. O problema principal era o uso do leitor
genérico de extrato bancário sobre uma fatura com várias seções e colunas.
O limite de R$ 50 mil, juros, pontos e simulações de parcelamento eram
incluídos como se fossem transações.

O leitor específico ancora a região financeira no Demonstrativo e nos
cabeçalhos de data, descrição, cidade e Crédito/Débito. Reconhece pagamento,
cashback, ajuste, anuidade, compras e parcelas. Subtotais e saldos anteriores
são controles. Limites e simulações não originam movimentos.

As compras somam R$ 846,98; as parcelas, R$ 800,57; a anuidade, R$ 12,50.
Os encargos correntes somam R$ 1.660,05. Os créditos são pagamento de
R$ 1.925,65, cashback de R$ 6,25 e ajuste de R$ 0,01. A conferência é:

```text
1.925,66 + 1.660,05 − 1.925,65 − 6,25 − 0,01 = 1.653,80
```

O OCR imprimiu uma compra como `62.50D`. A pontuação decimal é interpretada
como R$ 62,50 e conferida pelo subtotal de compras; nenhum dígito é
substituído para forçar um fechamento. Valores ou datas ilegíveis, sinais
inconsistentes, escrita manual em campos financeiros, subtotais ausentes e
campos sem geometria causam erro, sem queda para o leitor genérico.

A anuidade informa `04/12`, que é a parcela, e não tem data de lançamento.
Ela recebe a data de vencimento da fatura com a indicação no histórico
`sem data impressa; vencimento da fatura`, a evidência da data usada e o
aviso `invoice_due_date_used_for_undated_fee`. A prévia explica essa escolha
em português e recomenda conferir ou ajustar a data antes da exportação.
Esse alerta permanece mesmo com todos os valores financeiros conferidos.

Os gastos são negativos e os pagamentos/créditos positivos. Os saldos
exportados são de dívida: −R$ 1.925,66 e −R$ 1.653,80. O OFX usa o grupo de
cartão de crédito, `CCSTMTRS`, e não o grupo de conta corrente.

## Verificação e compatibilidade

Os testes sintéticos cobrem ordem dos objetos do PDF diferente da ordem
visual, colunas deslocadas no OCR, datas herdadas, referências, sinais,
totais brutos, saldos intermediários, controles ilegíveis, páginas e contas
misturadas, escrita manual e evidências de origem. Testes de HTTP real e
worker validam os mesmos leitores e os relatórios CSV, Excel e OFX.

O contrato HTTP atual usa `POST /convert`; `POST /analyze` já está removido
e retorna 405. A validação inclui conversão positiva de CSV, obtenção de
`GET /report/{analysis_id}`, análise inexistente com 404 e documentos com
controles inconsistentes com 400. A rota antiga não é reintroduzida.

Os layouts, métricas e avisos usam os contratos existentes. Não há migração
de banco nem necessidade de reprocessar registros existentes durante o
deploy. Para obter os resultados corrigidos, os arquivos anteriores devem
ser enviados novamente. O rollback consiste em reverter o commit desta
PR; os formatos persistidos continuam compatíveis.

Resultado final: **2.322 testes do backend passaram**, com um ignorado e
dois casos de falha esperada. Os dois avisos de depreciação FastAPI já
existiam. Os 48 testes novos e o teste de compatibilidade de poupança
Bradesco passaram por execução específica. A fixture existente de histórico
de login usa horários explícitos para tornar o teste cronológico estável.

No frontend, passaram 128 testes em 30 arquivos, lint, verificação de tipos,
checagem de sintaxe JavaScript, varredura UTF-8/mojibake e verificações de
navegação estática e Playwright. Ruff passou em todo o backend. As pastas
temporárias das execuções de pytest desta tarefa foram removidas.
