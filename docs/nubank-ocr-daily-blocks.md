# Nubank: movimentos e controles diários no OCR

O parser `layout_specific_nubank_ocr` trata extratos Nubank com o bloco
`Movimentações` e evidência geométrica Textract. O caminho de texto nativo
continua disponível. Os modos Textract `text` e `analysis` usam a mesma
conferência financeira específica.

Cada valor precisa estar na coluna monetária e alinhado com exatamente um
rótulo de movimento, total ou saldo. A seção `Total de entradas` ou
`Total de saídas` fornece a direção dos movimentos seguintes. Assim,
`Pagamento Recebido` entra como crédito, e um estorno de transferência
enviada continua sendo crédito quando impresso no bloco de entradas.

Os totais diários são controles, não movimentos ou saldos. `Saldo do dia`
é associado somente ao último movimento daquele dia. Cabeçalhos e rodapés
ficam fora das descrições, e a data/seção continua entre páginas, inclusive
quando a página seguinte começa pela continuação de uma contraparte ou
pelo saldo do dia anterior. A origem do movimento, do valor e de seu bloco
é preservada nos campos de proveniência já existentes.

A validação exige, com aritmética decimal:

- soma dos movimentos de cada bloco igual ao total impresso;
- abertura ou saldo diário anterior mais movimentos do dia igual ao próximo saldo;
- entradas e saídas do período iguais aos totais do resumo;
- fechamento do período igual à abertura mais entradas menos saídas;
- datas válidas, crescentes e dentro do período solicitado.

Valores ilegíveis, células duplicadas, falta de geometria, valores
manuscritos e controles inconsistentes exigem revisão. O erro financeiro
não deve cair silenciosamente no parser genérico. Não se inferem valores
ausentes pela diferença dos saldos.

A abertura e o fechamento impressos no resumo Nubank têm precedência
sobre a estimativa a partir de um movimento com saldo. Isso evita deduzir
uma abertura incorreta quando o primeiro saldo diário aparece somente
depois de vários movimentos. O resumo é lido antes de `Movimentações`.

## Evidência local

Os casos coletados foram reprocessados com os PDFs e a evidência OCR já
salvos, sem novas chamadas AWS ou inclusão de documentos de clientes no Git:

| Caso | Movimentos | Líquido | Alertas de consistência |
| --- | ---: | ---: | ---: |
| C03 | 47 → 47 | −R$ 299,16 | 21 → 0 |
| C20 | 21 → 23 | R$ 2.821,35 | 12 → 0 |

No C20, os pagamentos recebidos de R$ 1.234,74 e R$ 1.222,36 foram
recuperados diretamente das células impressas. A abertura é R$ 0,00 e o
fechamento R$ 2.821,35. No C03, a abertura é R$ 314,48 e o fechamento
R$ 15,32. Os testes versionados usam apenas dados sintéticos e exercitam
também os fluxos HTTP público e do worker, com downloads CSV/XLSX/OFX.

Não há migração de banco, mudança de contrato público ou novo serviço de
OCR. A correção vale para novos processamentos; resultados antigos exigem
reprocessamento. Rollback: reverter o commit e republicar API e worker.
