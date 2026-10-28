# Correções do terceiro lote de falhas de 28/10/2026

Complemento da PR #504, referente ao intervalo de 15h38 a 17h03, no horário
de São Paulo. As seis tentativas com captura representam cinco PDFs únicos.
Os originais e as evidências de OCR foram lidos em reprodução local; nenhum
histórico ou banco de produção foi consultado.

## Resultado dos cinco documentos

| Caso | Documento | Período | Páginas | Movimentos | Resultado e ação |
| --- | --- | --- | ---: | ---: | --- |
| D28-27 | Santander consolidado | Ago/2026 | 6 | 4 | Converte. O resumo usa “Saldo Disponível de Conta Corrente” e omite o total de débitos zero. Confere créditos, saldos da tabela e fechamento; limite de cheque e saldo disponível total ficam fora dos movimentos. |
| D28-28 | Sicoob SISBR | Ago/2026 | 29 | 479 | Converte. Associa o valor anterior à quebra de página à data, ao histórico e ao D impresso na seguinte. Quatro depósitos bloqueados ficam fora do caixa disponível, com aviso explícito. |
| D28-29 | BB Cash digitalizado | Ago/2026 | 4 | 87 | Converte com revisão. Recupera o traço do D reconhecido também como um “1” e uma marca “&” após um valor completo; exige evidência geométrica e saldo da linha. Conferir esses avisos no original. |
| D28-30 | BB nativo | Jul/2026 | 13 | 389 | Converte. Preserva os movimentos partidos entre páginas e o C/D independente. Dois depósitos bloqueados ficam fora do caixa disponível, com aviso explícito. |
| D28-31 | Bradesco net empresa | Jul/2026 | 24 | 967 | Converte com revisão obrigatória. Lê o período pela posição dos caracteres. Os saldos das linhas conferem; os totais impressos divergem dos movimentos efetivamente presentes. Conferir a integridade e os totais do documento. |

São 1.926 movimentos neste lote. O BB digitalizado é outro arquivo binário
em relação ao caso D28-20; não foi deduplicado por semelhança visual.

No Bradesco, os movimentos lidos somam R$ 54.070,91 de créditos e
R$ 56.811,81 de débitos. O PDF imprime R$ 105.682,08 e R$ 144.907,10,
respectivamente. A abertura de −R$ 36.472,01 e os movimentos extraídos
resultam no fechamento impresso de −R$ 39.212,91. O arquivo contém folhas
1/24 até 24/24; a causa da divergência não pode ser atribuída a páginas
ausentes apenas pela paginação. Nenhuma transação foi criada, removida ou
ajustada para forçar os totais. O aviso específico é
`printed_totals_differ_from_extracted_movements` e a validação global
continua sinalizada para revisão.

## Regras de associação e recuperação

- O BB e o Sicoob usam a mesma regra para um valor impresso antes da quebra
  de página: páginas adjacentes, um único valor excedente ao final da anterior,
  primeira linha seguinte sem valor e um C/D impresso alinhado à coluna.
  Saldos e aberturas não podem receber esse valor. As coordenadas físicas
  originais e referências às duas páginas são preservadas.
- Depósitos explicitamente bloqueados e marcados com `*` são contados e
  sinalizados separadamente. Um Pix com `*` não é tratado como depósito.
- A recuperação BB de `4,451 D` não autoriza arredondar valores com três
  casas decimais. Exige palavra numérica de baixa confiança, D independente
  de alta confiança sobreposto à sua borda, centavos já presentes e saldo
  impresso da própria linha que confirme a leitura. Ausência dessas provas
  mantém a ambiguidade. O texto capturado é preservado integralmente.
- Uma marca `&` após número e C/D completos só é aceita em OCR de baixa
  confiança, com saldo impresso independente. Não fornece valor ou sinal.
- As duas recuperações OCR do BB incluem `manual_review_recommended` e
  avisos específicos. Não se usa uma diferença de saldo para inventar valor.
- No Santander, omitir o total de débitos só equivale a zero quando todas as
  linhas estão legíveis, explicitamente na coluna de crédito e a soma e os
  limites da tabela conferem. A ausência do controle não permite recuperar
  uma tarifa ilegível nem ocultar um débito. Falhas desse leitor passam a
  registrar Santander na observabilidade, em vez da inferência Sisprime.

## Compatibilidade, armazenamento e implantação

Sem migração, alteração estrutural do banco, backfill ou reprocessamento
automático do histórico. Permanecem os limites atuais de OCR por plano,
o teto de páginas nativas e os contratos das APIs e exportações.

As cinco recusas por limite vistas no intervalo permanecem válidas. Seus
originais não foram obtidos; esta entrega não presume identidade ou período
para arquivos que só aparecem nos logs de recusa.

Os testes versionados usam documentos fictícios, incluindo HTTP real da
conversão, worker, relatório e exportações. O corpus de compatibilidade tem
48 cenários comparados à base #504. Os PDFs e resultados reais ficam em
diretório local ignorado; o replay reutiliza OCR capturado e Tesseract local,
sem novas chamadas ao Textract de produção.

Os 27 PDFs dos três lotes foram reexecutados, preservando 7.745 movimentos.
Foram conferidos os hashes dos originais e os downloads CSV, XLSX e OFX,
incluindo datas, sinais, valores, ordem e multiplicidade entre os formatos.
Os 967 movimentos do Bradesco permanecem iguais aos da leitura anterior;
o novo diagnóstico não altera seus valores. Nos scans anteriores do BB,
um C/D separado comprovado na posição da linha passa a ser lido diretamente,
dispensando o aviso de sinal inferido pelo rótulo de Pix quando essa prova
impressa está presente.

Implantar backend e worker depois das PRs anteriores. Reenviar os cinco
arquivos e conferir contagens, datas, sinais, avisos e os três formatos de
download. No Bradesco, a revisão financeira deve permanecer visível.
Conferir também que os arquivos acima do limite continuam recusados.

Para rollback, restaurar a imagem anterior do backend e worker. Não há
alteração persistente de dados a reverter. Os principais riscos são outras
geometrias de quebra de página e variações do OCR; associações ambíguas
continuam recusadas e recuperações confirmadas permanecem auditáveis.
