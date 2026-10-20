# Sicredi: colunas matriciais e totais diários

Dois formatos nativos usam caminhos específicos com controles impressos:

- `sicredi_matricial_paisagem_conta_corrente_v1`: o parser
  `layout_specific_sicredi_matrix` lê a posição das colunas DÉBITO,
  CRÉDITO e SALDO. A descrição não determina o sinal. O texto nativo
  mantém seus espaços para preservar essas posições.
- `sicredi_extrato_html_totais_diarios_v1`: o parser
  `layout_specific_sicredi_html` reúne descrições quebradas entre linhas,
  usa o valor monetário assinado e associa cada total diário ao último
  movimento do dia. Totalizadores ficam fora das transações.

No formato matricial, saldo anterior, saldos intermediários, transportes
entre páginas, saldo atual, total de débitos e total de créditos devem
conferir com os movimentos. A implementação específica exige o resumo
completo e colunas posicionadas; outros modelos matriciais continuam no
caminho existente. Datas inválidas permanecem disponíveis para validação.

No formato HTML, cada bloco diário e o total do período devem conferir
separadamente em entradas, saídas e saldo. Datas devem ser válidas e estar
no período indicado. Uma linha incompleta, sinal conflitante ou controle
inconsistente exige revisão, sem corrigir valores pela diferença do saldo.
Uma leitura validada não é substituída depois por um candidato genérico
escolhido apenas por quantidade de linhas.

## Casos de referência

Reprocessamento local dos originais coletados, sem novo OCR:

| Caso | Movimentos | Entradas | Saídas | Abertura | Fechamento | Alertas de saldo |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| C14 | 96 | R$ 84.111,23 | R$ 89.076,14 | R$ 3.982,13 | −R$ 982,78 | 10 → 0 |
| C21 | 8 | R$ 600,00 | R$ 679,94 | R$ 93,34 | R$ 13,40 | 3 → 0 |

O líquido correto é −R$ 4.964,91 no C14 e −R$ 79,94 no C21. O C21
anteriormente produzia nove movimentos ao importar totalizadores e perder
linhas legítimas. Os testes usam somente PDFs e textos sintéticos e
validam também upload público, worker e downloads CSV/XLSX/OFX.

Não há migração de banco, mudança de contrato público nem nova chamada
de OCR. Resultados já gerados permanecem inalterados e exigem
reprocessamento para receber a correção. Rollback: reverter o commit e
republicar API e worker.
