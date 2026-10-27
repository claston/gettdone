# Recuperação prioritária de 27/10/2026

Esta alteração trata seis documentos do recorte de recuperação encerrado em
27/10/2026 às 09:09, horário de São Paulo. Os originais e diagnósticos foram
consultados em leitura; somente os seis PDFs autorizados foram copiados para
`local/`, ignorado pelo Git. Os testes versionados usam dados fictícios.

## InfinitePay: trecho nativo de relatório

O arquivo contém as páginas impressas 11 a 20 de 27. Tem texto e coordenadas
nativos, mas não contém o resumo inicial do documento completo. A rota anterior
exigia os totais desse resumo para selecionar o leitor nativo; sem eles, o
arquivo acabava recusado pelo caminho de texto que exige coordenadas.

O reconhecimento existente do domínio InfinitePay e das colunas permite usar
as coordenadas reais também nesse trecho. O leitor separa os saldos diários dos
valores dos movimentos e preserva datas, sinais, horários, descrições e regiões
de origem. Datas e grupos podem continuar entre páginas. Valores órfãos, sinais
ausentes, horários inválidos e datas ilegíveis continuam impedindo a conversão.

O resultado local contém **135 movimentos**, **24 saldos diários impressos** e
**23 verificações independentes entre saldos**, sem divergência. O primeiro
saldo é apenas a referência inicial disponível; não prova o saldo anterior ao
primeiro movimento do trecho. O último dia está cortado antes de seu saldo.

Por isso, abertura e fechamento globais permanecem ausentes. O diagnóstico
aditivo `balance_bounds_unavailable=1` impede que o pipeline e o resumo XLSX
reconstruam essas fronteiras a partir de saldos intermediários. O OFX preserva
os movimentos sem publicar um `LEDGERBAL` não confirmado. Edições e regeneração
dos arquivos respeitam esse diagnóstico; saldos informados explicitamente pelo
usuário continuam permitidos. As evidências dos saldos diários permanecem nas
linhas. O aviso de documento parcial continua ativo, inclusive após exportação.

Extratos InfinitePay completos continuam exigindo o resumo financeiro e os
controles diários. Uma ausência de resumo sem evidência de documento parcial
não é convertida silenciosamente em trecho válido. Divergências aritméticas
legíveis permanecem sinalizadas, sem alteração de números para fechar contas.

## Detalhe de conta em ordem decrescente

O segundo PDF foi inicialmente associado a PagBank/PagSeguro por texto de uma
contraparte. A conferência visual do original identifica o logotipo Santander;
o logotipo não aparece como identificação bancária no texto extraído. Esta
correção não atribui banco ou código bancário a partir de uma contraparte.

O novo modelo estrutural `account_detail_descending_cash_v1` exige o resumo
Saldo inicial/Entradas/Saídas/Saldo final, período legível e as colunas
Data/Descrição/Valor/Saldo em todas as páginas. Cada valor e saldo financeiro
precisa corresponder exatamente a um movimento datado. A descrição de duas
linhas é preservada, inclusive a parte acima da linha de data/valor.

A ordem física decrescente é preservada. A conferência financeira percorre o
encadeamento no sentido contábil e compara todos os saldos e os quatro controles
do resumo. O resultado local mantém **58 movimentos**, com os totais impressos
conferidos e **zero divergências**, em lugar dos 56 alertas anteriores. Não se
remove o aviso quando o documento contém uma diferença real: os testes alteram
saldos, totais e período e verificam a preservação dos movimentos e da revisão.

## Bradesco mensal: continuação e saldo anterior sem data

Os dois arquivos Bradesco também continuavam recusados na versão atual. No
primeiro, parte do CNPJ repetido no cabeçalho de continuação era interpretada
como documento de um movimento. O contador `Folha`, quando impresso no topo,
agora delimita a área do livro antes da procura de valores e referências. Um
contador no rodapé não corta os movimentos da página.

Há descrições divididas em duas linhas, acima e abaixo do valor. A leitura da
faixa completa já existia, mas uma exigência adicional de texto exatamente na
linha do valor ainda recusava esses movimentos. No mensal, a descrição pode
ocupar essa faixa quando existe exatamente uma referência de documento junto
ao valor. Referências órfãs e valores sem associação continuam sendo recusados.

O segundo arquivo imprime `Saldo anterior` sem data nessa linha. Esse saldo
explícito é aceito na coluna correta do mensal, exigindo período completo e
válido no cabeçalho. Não é criada uma data para o saldo. Movimentos continuam
exigindo uma data legível, própria ou herdada da sequência impressa, e ordem
cronológica consistente. Datas legíveis fora do período permanecem sinalizadas.

O total da coluna Débito pode ser impresso como magnitude positiva ou com sinal
negativo. A conferência usa a magnitude dessa coluna, preservando os sinais dos
movimentos. Isso elimina um falso apontamento no primeiro arquivo, sem mudar
qualquer valor financeiro para forçar fechamento.

| Caso | Resultado local |
| --- | --- |
| Bradesco, 10 páginas | 345 movimentos; todos os saldos e totais conferidos; nenhum alerta |
| Bradesco, 12 páginas | 376 movimentos; saldos por linha conferidos; revisão mantida por divergência real dos totais impressos |
| Bradesco, 5 páginas | 148 movimentos preservados integralmente; saldos e totais conferidos; falso alerta eliminado pelo mesmo ajuste do total de débito |

A contagem foi conferida independentemente contra as células do texto nativo,
incluindo datas, valores, sinais, multiplicidade, ordem e cada saldo. XLSX e OFX
preservam todos os movimentos do período. Os anexos `Últimos Lançamentos` e
`Saldos Invest Fácil / Plus` continuam separados do livro exportado, inclusive
quando começam na mesma página que a continuação do período.

No terceiro Bradesco, a comparação independente confirmou os mesmos 148
movimentos financeiros da captura, na mesma ordem, sem alterar datas ou valores.
Repor isoladamente a comparação anterior do total de débito faz o aviso
financeiro reaparecer sem mudar nenhum movimento. O ajuste já implementado
resolve também esse documento; não foi necessária outra alteração no parser.
O XLSX e o OFX foram conferidos com os 148 movimentos do período, excluindo os
anexos posteriores. O resultado histórico capturado permanece inalterado.

## Uniprime: extrato seguido de posição consolidada e juros

O arquivo de três páginas contém o livro de janeiro nas duas primeiras e
informações de posição consolidada/juros após o título explícito
`Consulta Posição Consolidada`. A terceira página repete a data de impressão,
de outro mês. Como ela não repete o cabeçalho da tabela de movimentos, o leitor
anterior interpretava essa data como um movimento e recusava o arquivo por
estar fora do período do extrato.

A seção de movimentos termina nesse título, desde que ele esteja fora da
coluna de descrição. Um lançamento com o mesmo texto no histórico não encerra
a leitura. As páginas e linhas posteriores ainda são verificadas: outro
cabeçalho de movimentos ou valores na coluna de movimentação impedem que
operações sejam omitidas silenciosamente. Sem o delimitador explícito, uma data
incompatível continua recusada. Sinais, centavos, recuperação identificada e
validação financeira preservam os critérios existentes.

A referência de documento usa o cabeçalho `Nº Doc` quando ele está presente,
em vez de um deslocamento fixo a partir do título `Data`, que pode estar
centralizado. Isso preserva números e referências textuais com seus zeros e
evidências originais. O comportamento anterior permanece como alternativa nos
modelos que não apresentam esse cabeçalho.

O resultado local contém **50 movimentos e 50 referências**, todos conferidos
independentemente contra o texto nativo: datas, sinais, valores, ordem e cada
saldo impresso. Abertura e fechamento correspondem ao extrato de janeiro; o
saldo da posição de dezembro não substitui seu fechamento. XLSX e OFX preservam
os 50 movimentos, sem OCR remoto e sem alertas. Não há alteração do PDF original
ou reprocessamento de resultados históricos em produção.

## Validação e compatibilidade

- TDD: a primeira execução dos novos testes apresentou 12 falhas e 7 sucessos,
  reproduzindo a recusa InfinitePay, valores/saldos confundidos e limites do
  leitor genérico. A etapa HTTP também reproduziu a inferência incorreta dos
  saldos globais antes da correção.
- Bradesco: os novos cenários reproduziram inicialmente 10 falhas e 4 sucessos;
  um teste adicional reproduziu o falso aviso de total de débito negativo.
  A validação focada final apresentou 66 sucessos, incluindo HTTP real.
- Uniprime: o cenário inicial apresentou 5 falhas e 5 sucessos; o teste de
  referências centralizadas falhou antes do ajuste correspondente. A validação
  focada final apresentou 91 sucessos, incluindo HTTP, exportações, sinais,
  centavos truncados, anexos e referências preservadas.
- Os testes HTTP reais cobrem `/convert`, `/report/{id}`, CSV, XLSX, OFX, sinais,
  quantidades, ausência de saldos não comprovados, relatório inexistente `404`
  e o endpoint legado `/analyze`, que permanece desativado com `405`.
- Testes de edição verificam a preservação dos controles impressos e de saldos
  globais ausentes, regeneração de arquivos e saldos manuais explícitos.
- As rotas nativas não solicitam OCR remoto. Não há mudança da conciliação,
  migração de banco, backfill ou reprocessamento de resultados históricos.
- O campo de diagnóstico usa o dicionário existente de métricas, com zero por
  padrão na ausência de informação. Clientes anteriores permanecem compatíveis.

Executar a suíte completa e Ruff pelo Python do ambiente virtual, além dos
lints de texto e navegação. No Windows, pytest deve ser executado fora do
sandbox com um `--basetemp` específico, removido após a validação. O corpo da
PR registra os comandos e resultados finais.

## Implantação e rollback

O risco é médio por afetar parsing financeiro e exportação de trechos. O novo
formato exige uma estrutura explícita; os leitores existentes preservam seu
comportamento por padrão. Monitorar falhas por modelo, diferenças financeiras,
avisos de documentos parciais e a ausência de saldos globais nos trechos.

Para disponibilizar a correção, API e worker precisam receber a mesma versão
em uma implantação autorizada. Nenhuma implantação, promoção de imagem ou
alteração de produção foi executada durante esta investigação.

O rollback consiste em restaurar as versões imutáveis anteriores da API e do
worker. Não há rollback de esquema. Históricos e artefatos já gerados permanecem
como foram gravados e exigem decisão explícita antes de reprocessamento.
