# Investigação dos casos restantes — 14/11/2026

Esta rodada examina os **44 documentos únicos restantes** do levantamento do bucket `gettdone-production-ai-recovery-recoverybucket-d1pyhcu9bgak`, em `us-east-1`. A referência é a captura de inventário de 14/11/2026 às 13:55 UTC; não é uma nova contagem em tempo real do bucket.

A rodada anterior está no [PR #592](https://github.com/claston/gettdone/pull/592). Esta alteração parte de `origin/main` (`09d0f7d26f083986ec0230d72bcc9a058df5a805`) e não incorpora aquele PR. Nenhuma alteração foi aplicada à produção ou aos objetos S3.

## Resultado

Dez documentos antes recusados, correspondentes a **20 capturas**, passam na execução local: oito extratos com **1.667 movimentos** e dois extratos que declaram explicitamente ausência de movimentação. Os demais **34 documentos permanecem recusados nesta versão**; a tabela abaixo registra a causa observada e o trabalho ainda necessário. Isso não significa que os 34 sejam ilegíveis ou que todas essas recusas sejam justificadas.

Os arquivos originais, OCR e resultados detalhados permanecem em diretório temporário privado fora do repositório. Os identificadores abaixo são prefixos dos identificadores de captura, não nomes de clientes. Os testes versionados usam apenas documentos fictícios.

## Correções verificadas

| Captura | Documento | Movimentos | Causa e tratamento |
| --- | --- | ---: | --- |
| `8ab8f09b6b31` | BB corrente + Rende Fácil | 31 | Agência sem dígito ou zero à esquerda aceita como origem; sinais impressos preservados. |
| `a38c1df53e9e` | BB corrente + Rende Fácil | 44 | Mesma variação de agência. |
| `50c6418cf810` | BB corrente + Rende Fácil | 651 | Saldo anterior sem documento reconhecido como posição, sem virar movimento. |
| `b45b2b17790a` | BB corrente + Rende Fácil | 778 | Mesmo controle de saldo anterior sem referência. |
| `dd95e75853ed` | BB em inglês | 68 | Tipo CHECK aceita os sinais impressos de cheque e devolução; saldo atual negativo é lido. Contagem e totais continuam obrigatórios. |
| `f10b4c517b65` | Credisis | 4 | Código de referência ausente não impede um saque com data, descrição, valor e saldo próprios. Nenhum código é inventado. |
| `07913fecf848` | Sicoob SISBR nativo | 19 | Rótulo alternativo do saldo em conta reconhecido, inclusive sem espaço antes de `(+):`. Limite e saldo disponível continuam distintos. |
| `077075082753` | Sicoob SISBR OCR | 72 | Saldo bloqueado inicial com espaço após ponto; valor longo alinhado à direita reconhecido pela posição real. |
| `256bf26907bd` | Nubank sem movimentação | 0 | Declaração explícita, totais zerados, três impressões de saldo concordantes e período confirmado. |
| `4c547fe753b0` | Nubank sem movimentação | 0 | Mesma validação para conta com saldo zero. |

Nos quatro BB com Rende Fácil, uma verificação independente das linhas do PDF confirma o mesmo multiconjunto de datas e valores, incluindo repetições legítimas. O saldo combinado de conta e investimento continua sem ser usado como saldo global de caixa. No BB em inglês, a abertura continua inferida e sujeita a revisão. As diferenças financeiras reais continuam sinalizadas.

A associação de colunas SISBR é compartilhada entre PDF nativo e OCR. A separação de histórico e valor mesclados usa palavras e suas coordenadas existentes, sem recuperar um sinal pela descrição ou pelo saldo. Os testes também cobrem referência ausente, sinais conflitantes, tipo desconhecido, divergência real e evidência incompatível com uma declaração de inatividade.

## Casos ainda pendentes

| Captura(s) | Evidência e causa observada | Próximo tratamento necessário |
| --- | --- | --- |
| `11a022ac3849` | Relatório Banrisul de operações Pix, com datas fragmentadas na camada textual e linhas quebradas. | Associar células pela geometria e validar todas as operações efetivadas, sem juntar dígitos por suposição. |
| `2e4a4e2d2be0`, `609371e51905`, `81de8687b262` | Fundos BB: data/hora da impressão é tratada como linha financeira; cabeçalhos de impostos e detalhes de aplicação também variam. | Preservar posições, cotas, resgates, impostos e aplicações utilizadas no modelo de relatório de investimentos; não convertê-los automaticamente em caixa. |
| `14751351836a`, `9f88da0e6cda` | SICREDINVEST em relatório diário simplificado; leitor atual espera outra matriz mensal. | Adicionar representação diária com controles próprios de abertura, aplicações, resgates, rendimentos e fechamento. |
| `717afeb11e6c` | Resumo de vendas por bandeira e modalidade, sem movimentos bancários individuais datados. | Tratar como relatório agregado; não inventar datas ou recebimentos bancários. |
| `85efbf773329` | Imagem de declaração escolar, confirmada visualmente. | Rejeição como extrato é adequada. Melhorar classificação/mensagem se necessário. |
| `0d918f85507b`, `be0becac7fea`, `048babb0e65f` | Documentos fiscais identificados como conteúdo não suportado. | Manter distinção de nota fiscal e extrato; eventual suporte fiscal deve preservar seu significado. |
| `48de00440c71` | Tabela de tributação de produtos, com códigos fiscais e alíquotas. | Classificar como relatório fiscal; não criar movimentos de caixa a partir de códigos ou percentuais. |
| `ceadff75fd11` | Sicredi OCR contém saldo com separador decimal corrompido e texto sobreposto. | Conferir os glifos e regiões; só recuperar pontuação com evidência inequívoca, sem ajustar valores para reconciliar. |
| `8a626caaa38a` | Bradesco digitalizado com OCR incorporado, dias sem mês na linha e cabeçalhos mensais distintos. | Usar blocos mensais e procedência; verificar discrepâncias de saldo sem descartá-las. |
| `ad136a419d3d` | C6 com datas completas e mudança de mês, mas sem o cabeçalho mensal exigido pelo leitor. | Separar metadados opcionais de período da data explícita do movimento; verificar cobertura do trecho. |
| `1489dc4e2a8c` | Extrato com grupos diários, bloqueios/desbloqueios judiciais e débito de IOF; layout genérico não associa a tabela. | Modelar controles não monetários separadamente do débito efetivo, com reconciliação. |
| `b46d5a12b64a` | Sicredi OCR: segunda página de continuação sem repetição das colunas. | Herdar colunas somente com continuidade e posições confirmadas; preservar todas as linhas posteriores. |
| `0d1276da30a1` | Nubank OCR apresenta totais com sinais duplicados/ausentes e erro de associação de saldo diário. | Conferir texto e palavras do OCR contra a imagem; totais e sinais não podem ser inferidos para fechar o saldo. |
| `1c5c0499ce20`, `68ead4a02ff1` | Sicredi matricial OCR: cabeçalho de documento/histórico mesclado não é reconhecido. | Reconstituir colunas com evidência das palavras e validar todas as páginas. |
| `9465ae413357` | Caixa OCR inclui movimentos posteriores ao período impresso e variantes/fragmentos de moeda e sinal. | Distinguir cobertura declarada das datas efetivas, mantendo revisão de cobertura e sinais ambíguos explícitos. |
| `74da0b4819f1` | PagBank impresso horizontalmente em partes: descrições em páginas 1–2 e valores em páginas 3–4. | Associar as partes da mesma tabela com prova de correspondência de linhas; nunca parear somente pela quantidade. |
| `00782f7816db` | SISBR OCR: corrigidos rótulo e histórico/valor mesclados; permanece saldo diário sem data própria, dentro da faixa do movimento anterior. | Reconhecer controles sem data usando contexto inequívoco do bloco e conferir o restante do arquivo. |
| `5155edef0256` | SISBR visualmente legível, mas texto extraído tem mapeamento de caracteres corrompido. | Recuperar o mapeamento da fonte ou usar OCR; não substituir caracteres por uma tabela presumida. |
| `5b47cb2131d4` | Extrato visualmente legível com fonte corrompida; arquivo começa em página numerada 8/15. | Recuperar texto e manter indicação de cobertura parcial; não declarar abertura global validada. |
| `33e8c6a4f0cd` | Santander: associação de valores/saldos falha; datas visíveis ultrapassam o período do cabeçalho. | Verificar cada coluna e o escopo dos saldos, preservando revisão de cobertura. |
| `344b33c020f4`, `3758dedbd8ea`, `8febc334199f` | Extratos Itaú digitalizados sem camada textual utilizável. | Obter OCR completo de todas as páginas; a execução desta rodada ficou offline e não solicitou novo OCR pago. |
| `4515fa883a67` | Sicoob moderno OCR: saldo diário sem data cai na faixa de uma tarifa e gera dois valores candidatos. | Separar saldo e movimento por rótulo/posição e contexto do bloco; conferir sinais e continuidade. |
| `d54b82b77ccd` | InfinitePay: vírgula do saldo inicial está presente como glifo isolado, fora da linha numérica gerada. | Corrigir agrupamento geométrico de pontuação e verificar todos os totais/movimentos, sem deduzir a vírgula pelo valor esperado. |
| `112d35108384` | Itaú completo: tabela após o resumo contém operações futuras/agendadas, recusadas como linhas extras. | Identificar o escopo explícito dos agendamentos e preservar a informação sem contá-los como caixa já efetivado. |
| `2ecb8903c8f5` | SISBR de 26 páginas com camada OCR embutida e sinais/glifos corrompidos. | Conferir imagens e referências de todas as páginas; rejeitar campos realmente ambíguos, sem publicar extração parcial como completa. |
| `19fafffada34` | SISBR de 51 páginas reúne extratos mensais independentes; leitor exige um único período/resumo. | Segmentar blocos da mesma conta, reconciliar cada bloco e verificar continuidade, sem exigir separação manual por mês. |

As seis entradas de outra natureza (três documentos fiscais, uma tabela tributária, um resumo agregado e uma declaração escolar) não comprovam movimentos bancários individuais. As outras 28 pendências exigem trabalho no leitor, na representação do relatório ou na evidência textual/OCR. Os exemplos de revisão legítima não justificam omitir movimentos legíveis.

## Validação e implantação

Testes de regressão foram executados antes das correções e reproduziram as falhas. A validação final inclui suíte completa, lint, varredura de texto UTF-8/mojibake, compatibilidade de PDF/revisão e HTTP real com PDF e CSV, consulta de relatório, exportação CSV/XLSX/OFX e erro de relatório inexistente. O endpoint legado `POST /analyze` está desativado na versão atual e retorna 405; o fluxo público equivalente é `POST /convert`.

Não há migração de banco nem alteração de armazenamento. A publicação exige o fluxo habitual de revisão e implantação; reverter o commit restaura os leitores anteriores. Reprocessar arquivos em produção é uma operação separada, não executada nesta auditoria.
