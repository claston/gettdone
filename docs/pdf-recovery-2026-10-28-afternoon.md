# Correções do segundo lote de falhas de 28/10/2026

Esta PR complementa as correções de período opcional, múltiplos meses e
leitores do primeiro lote (#501–#503). O levantamento de referência cobre
11h50–15h38, horário de São Paulo: 13 falhas de leitura em dez PDFs distintos
e cinco tentativas recusadas por limites de páginas.

## Escopo e limites preservados

Os PDFs digitalizados de 20 e 23 páginas continuam sujeitos ao limite de OCR
do plano. As tentativas de 283 e 677 páginas continuam acima do teto atual
de 250 páginas para PDF com texto. Esta entrega não altera esses limites,
os planos, as cotas, a cobrança nem as regras de tamanho do upload.

A identificação dos arquivos recusados por limite continua pendente. Eles
não têm original nas capturas usadas neste levantamento; não houve consulta
ao histórico de produção nem acesso ao banco de dados.

## Comportamento corrigido

- **Geometria de scans:** associa valores à linha mais próxima e considera a
  inclinação do cabeçalho. No Caixa, datas e históricos independentes mostram
  a variação de perspectiva ao longo da página. Não usa diferenças de saldo
  para escolher valores, dígitos ou sinais. Um C/D separado só é associado
  quando está impresso imediatamente ao lado do número.
- **Abertura BB:** reconhece a linha explicitamente rotulada `Saldo Anterior`
  na coluna de valor, na coluna de saldo ou nas duas. Cópias divergentes
  continuam ambíguas. A abertura não vira uma transação. Referências e
  fechamento seguem a geometria da linha, inclusive em páginas sem cabeçalho
  repetido.
- **Sicredi:** lê o total à esquerda da divisória do quadro lateral de
  encargos. Barras `|` dentro do histórico continuam fazendo parte do
  lançamento. Movimentos dos nove meses são preservados; valores previstos
  no quadro lateral não entram no caixa efetivado.
- **Sicoob:** considera o alinhamento à direita da coluna financeira para
  valores longos que começam antes do título `VALOR`. Mantém a exigência de
  um valor e sinal inequívocos por movimento, além dos controles diários.
- **Mercado Pago:** reutiliza as colunas comprovadas na continuação sem
  cabeçalho. Ainda exige datas, IDs, valores, saldos e limites das linhas
  impressas, e compara os campos financeiros com a outra leitura nativa.
  Reembolso e débito com ID compartilhado continuam sendo movimentos distintos.
- **Páginas brancas:** ignora uma página sem caracteres apenas quando sua
  renderização inteira é branca, mesmo contendo um objeto de imagem. Mantém
  o número físico da página. Imagens com conteúdo continuam exigindo leitura.
- **Lote/origem:** reconhece o arranjo com lote e origem antes da data,
  ano abreviado confirmado pela emissão e débito com sinal ao final.
  Normaliza a rotação internamente, mantendo as coordenadas da aparência
  original e o hash do arquivo recebido.
- **Banrisul mensal:** associa dias ao mês/ano impressos, mantém referências,
  históricos e continuações de página, e confere os saldos. Uma camada de OCR
  invisível com valores truncados exige leitura da imagem. Não completa os
  dígitos omitidos pela camada de texto.
- **Múltiplos meses:** os novos modelos usam a separação compartilhada de
  blocos. Cobertura mensal explicitamente rotulada é reconhecida; emissão
  não é tratada como cobertura. Blocos sem continuidade comprovada mantêm
  aviso de revisão e não anunciam um saldo global validado.

## Replay local dos dez PDFs

| Caso | Documento | Período | Páginas | Movimentos | Resultado |
| --- | --- | --- | ---: | ---: | --- |
| D28-13 | Caixa Gerenciador | Jul/2026 | 1 | 14 | Converteu usando o OCR capturado; separadores e sinal separado conferidos. |
| D28-14 | Sicredi matricial | Jan–set/2026 | 21 | 935 | Converteu; resumo e movimentos separados do quadro lateral. |
| D28-15 | Lote/origem, referência BRB | Jan/2026 | 1 | 29 | Converteu; ano, sinal ao final e rotação preservados. |
| D28-16 | Sicoob SISBR | Ago/2026 | 28 | 357 | Converteu; valores longos e controles diários conferidos. |
| D28-17 | Banrisul mensal | Abr/2026 | 3 | 61 | Converteu usando OCR visual local da imagem original. |
| D28-18 | Sicoob SISBR | Jul/2026 | 7 | 65 | Mantido o sucesso da #503. |
| D28-19 | Mercado Pago | Set/2026 | 3 | 30 | Converteu; mantém revisão por referências compartilhadas. |
| D28-20 | BB Cash digitalizado | Ago/2026 | 4 | 87 | Converteu usando o OCR capturado; mantém revisão por sinal associado ao rótulo explícito de Pix. |
| D28-21 | Sicoob SISBR | Set/2026 | 2 | 4 | Converteu; segunda página comprovadamente branca. |
| D28-22 | Sicoob SISBR | Set/2026 | 30 | 361 | Mantido o sucesso da #503. |

São 1.943 movimentos no segundo lote. O replay preserva os avisos existentes;
o resultado dos controles não representa uma conferência humana de cada linha.

Os 12 PDFs do primeiro lote também foram reexecutados. No conjunto, os 22 PDFs
mantiveram 5.819 movimentos e exportações CSV, XLSX e OFX completas, com datas,
sinais, valores e multiplicidade preservados entre os formatos. O corpus
fictício de compatibilidade cobre 34 cenários, incluindo o leitor Banrisul
Portal anterior, sem regressões de movimentos em casos já corretos.

O modelo lote/origem não presume a identidade bancária a partir de nomes de
bancos no histórico. Metadados ausentes continuam disponíveis para conferência
pelo usuário, sem impedir a extração dos movimentos legíveis.

As capturas reais e exportações ficam somente em diretório local ignorado.
Os testes e o corpus versionados são fictícios. A validação Banrisul usa
Tesseract local; não houve nova chamada ao Textract em produção. A resposta
real do Textract para esse arquivo deve ser conferida após a implantação.

## Compatibilidade e implantação

Sem migração, alteração de estrutura do banco, backfill ou reprocessamento
automático de históricos. As métricas usam campos existentes. Os downloads
continuam com os mesmos contratos CSV, XLSX e OFX.

Implantar com as dependências anteriores no backend e no worker. Reenviar os
arquivos após a implantação; conferir contagem de movimentos, saldos, sinais,
avisos de revisão e downloads. Confirmar também que os limites continuam
recusando os arquivos acima da capacidade contratada.

Para rollback, restaurar a imagem anterior do backend/worker. Não há alteração
persistente de dados a reverter. Os pontos de atenção são a geometria de
outros scans e variações do OCR; valores ilegíveis continuam exigindo revisão.
