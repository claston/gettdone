# Falhas de conversão de 27/10/2026 — tratamento em três lotes

## Escopo e dependência

Recorte congelado de 27/10/2026, de 00h00 a 17h24min53s, em
`America/Sao_Paulo`. A contagem de 32 falhas de extrato bancário corresponde a
18 PDFs distintos por SHA-256. São tentativas, incluindo reenvios do mesmo PDF.

O Bradesco D27-13, de 33 páginas e dois envios, já é atendido pela PR #497.
Esta continuação trata os outros **17 PDFs / 30 tentativas**, em uma única PR
com três lotes. Sua base é a branch da #497; a dependência deve entrar primeiro
na `main`. Não há merge, deploy ou reprocessamento de produção neste trabalho.

Os identificadores D27 referem-se ao dia do envio, não ao mês dos lançamentos.
Documentos e evidências reais permanecem em arquivos locais ignorados pelo Git.
As regressões versionadas usam somente dados fictícios.

## Resultado por documento

| Caso | Documento | Envios com falha | Resultado local |
|---|---|---:|---|
| D27-03 | BRB, 8 páginas | 1 | 265 lançamentos; mantém a indicação de ausência de controles de saldo impressos. |
| D27-04 | Banco do Brasil, 3 páginas | 3 | 64 lançamentos; usa a data de movimento explícita no OCR. |
| D27-05 | Banco do Brasil, 11 páginas | 2 | 336 lançamentos; separa histórico, documento e dinheiro pelas palavras e suas coordenadas. |
| D27-06 | BTG Pactual, 2 páginas | 1 | 19 lançamentos; aceita a ordem crescente e conserva a revisão por diferenças nos controles impressos. |
| D27-07 | Banco do Brasil, 9 páginas | 2 | 249 lançamentos; conserva a nona página vazia e as referências das oito páginas com dados. |
| D27-09 | Sicoob, 5 páginas | 4 | 115 lançamentos; separa data/documento mesclados e conserva a ordem crescente. |
| D27-10 | Caixa, 5 páginas | 1 | 95 lançamentos; preserva períodos independentes, datas impressas e revisão de limites de período. |
| D27-11 | Caixa, 4 páginas | 1 | 26 lançamentos; confere períodos independentes, inclinação e indicadores cortados com os demais valores. |
| D27-14 | Mercado Pago, 7 páginas | 3 | 87 lançamentos; confere as colunas OCR, cada saldo de linha e os totais do período. |
| D27-15 | XP Empresas, 1 página | 2 | Sem movimentação confirmado; preserva os saldos não nulos do resumo. |
| D27-16 | XP Empresas, 1 página | 1 | Sem movimentação confirmado; preserva os saldos não nulos do resumo. |
| D27-17 | Exportação sem sinais, 4 páginas | 1 | Mantém a solicitação explícita do original com sinais ou colunas de débito/crédito. Não deduz a direção pelo histórico. |
| D27-18 | Bradesco — investimentos, 1 página | 1 | Recusa tipada como `investment_summary`; o admin usa “Resumo de investimentos”, já contemplado na #497. |
| D27-19 | Bradesco — sem lançamentos, 1 página | 1 | Sem movimentação confirmado; não transforma o retrato de fundos disponíveis em saldo do período. |
| D27-20 | Caixa com fonte ilegível, 2 páginas | 2 | Encaminha à leitura visual; OCR local recupera 16 lançamentos, incluindo a seção de lançamentos do dia. |
| D27-21 | Banestes, 1 página | 2 | 6 lançamentos; associa dia/mês, confere os saldos Conta/Rende+ e separa o rendimento previsto. |
| D27-23 | Nubank PJ com fonte ilegível, 4 páginas | 2 | Encaminha à leitura visual; OCR local recupera 18 lançamentos e conserva a revisão financeira. |

Total: **12 PDFs com 1.296 lançamentos**, **3 PDFs sem movimentação confirmada**,
**1 recusa pelo tipo de documento** e **1 solicitação de original com sinais**.
Isso cobre as 30 tentativas desta PR. As duas tentativas D27-13 continuam na #497.

## Lote 1 — extração e datas

- Detecta camadas que devolvem nomes numéricos de glifos (`/0`, `/1`, `/i255`)
  em vez de texto, respeitando os limites existentes de páginas de OCR.
- Uma página só pode permanecer vazia na fonte nativa quando não possui objetos
  e sua renderização é inteiramente branca. Imagens não são descartadas.
- O novo leitor Banestes separa os dias e meses das movimentações, confere os
  totais de entradas/saídas e mantém rendimento previsto fora dos lançamentos.
  O saldo Conta/Rende+ combinado permanece como evidência; não se transforma
  em saldo de caixa no OFX. A revisão informa que há aplicações no saldo.

## Lote 2 — tipo do documento e ausência de movimentação

- Os XP exigem declaração explícita de ausência de extrato, período válido,
  entradas/saídas zeradas e igualdade dos saldos inicial/final.
- O Bradesco exige as duas declarações de ausência de lançamentos, a folha
  completa e os controles do resumo. Os fundos disponíveis são um retrato atual,
  não uma prova do saldo inicial/final do período consultado.
- O caminho nativo conserva as coordenadas das colunas e recusa uma imagem
  adicional na área que poderia conter movimentações.
- A identificação de posição Bradesco torna-se uma recusa tipada, preservando
  extratos de conta com apêndices de investimentos. Versão do classificador:
  `2026-10-27.1`.

## Lote 3 — lançamentos e controles

- BRB aceita uma linha de data/valor cujo histórico está impresso separadamente.
- Banco do Brasil usa as palavras das colunas, mantém o indicador C/D junto ao
  valor e conserva complementos de histórico que atravessam páginas.
- Sicoob aceita a ordem crescente ou decrescente comprovada pela posição do
  saldo anterior; data e documento mesclados mantêm seus tokens e referências.
- BTG normaliza a direção do quadro pela abertura e pelo fechamento explícitos.
- Caixa conserva os períodos independentes, as seções históricas e do dia e
  sinaliza datas de movimento impressas na primeira semana após o período.
- Indicadores de saldo ilegíveis só podem ser recuperados quando os valores
  impressos e o saldo anterior determinam uma única solução. Dígitos ilegíveis,
  sinais conflitantes e soluções ambíguas continuam recusados.
- Mercado Pago exige cobertura de datas, referências e células monetárias.
  Marcas OCR extras junto ao dinheiro só são aceitas quando o saldo da linha
  confirma exatamente o número lido; o texto original permanece preservado.
- Nubank associa os resumos pelas coordenadas quando a ordem OCR separa os
  blocos de rótulos e valores. Mês/ano sem espaço conserva os dígitos impressos.

Não são criados lançamentos para compensar diferenças financeiras, rendimentos
sem data de movimento ou limites desconhecidos. As revisões reais permanecem.

## Evidências e validação

- SHA-256 dos 17 originais conferido antes do replay.
- Nenhuma chamada remota de OCR, alteração no S3 ou gravação em produção.
- As evidências OCR já capturadas foram reutilizadas. D27-20 e D27-23 também
  foram renderizados e lidos com Tesseract local em português/inglês; o gateway
  remoto permaneceu bloqueado. A chamada ao OCR de produção não foi executada.
- Os 1.296 lançamentos conservam referências e regiões de origem. Os valores
  possuem testemunhas monetárias nas evidências capturadas ou na extração
  independente das regiões do PDF. BRB e BTG também têm suas sequências de
  datas/valores comparadas com uma leitura separada dos originais.
- CSV, XLSX e OFX foram comparados com as sequências de datas e valores dos
  15 documentos aceitos, incluindo os três vazios; FITIDs permanecem únicos.
- No Bradesco sem movimento, API e OFX conservam a ausência de saldo do período:
  nenhum `LEDGERBAL` é inventado.
- Regressões fictícias cobrem campos ausentes, imagens ocultando lançamentos,
  indicadores ambíguos, valores inconsistentes e preservação da fonte original.
- Testes de HTTP real cobrem upload, relatório, CSV/XLSX/OFX e ID inexistente.
  O CSV existente permanece compatível. `POST /analyze` continua sendo a rota
  legada desabilitada com HTTP 405; o fluxo atual usa `POST /convert`.

Os resultados finais da suíte e dos linters estão registrados no corpo da PR.

## Produção e rollback

Não há migração de banco, alteração de schema, reclassificação retroativa do
histórico ou mudança de contrato HTTP. As 32 falhas históricas não desaparecem
apenas pela publicação do código. Um novo processamento é necessário para
produzir outro resultado; nenhum foi disparado neste trabalho.

Risco principal: variações de OCR e geometria fora dos modelos auditados.
As proteções de legibilidade, cobertura de linhas, ambiguidade e controles
financeiros permanecem ativas. Limites de páginas de OCR não foram ampliados.
Rollback: reverter os três commits desta continuação, mantendo a base da #497.
Após deploy, conferir conversões novas e suas sinalizações, sem usar o total
histórico de falhas como prova de regressão ou de recuperação.
