# Capturas de 20/10/2026: layouts, OCR e qualidade dos dados

## Recorte e método

Foram comparadas as capturas posteriores à primeira consulta do dia 20, cuja última entrada era das 09h15 de Brasília.
O novo recorte termina na captura das 13h38: **35 capturas, 33 PDFs distintos, dos quais 32 inéditos**.
Os três reenvios são um Itaú já analisado, uma repetição do Sicredi vetorial e uma repetição do Stone com falha.
Reenvio não equivale a um novo problema de layout.

A auditoria usa os documentos originais e os artefatos determinísticos/OCR preservados no S3.
Os documentos e identificadores pessoais permanecem fora do repositório; os testes versionados são sintéticos.
O código anterior às PRs #456–#458 foi reproduzido a partir de `823e32f`, comparado com a base pós-merge `5bb5983` e com esta correção.
Não foi encontrado um defeito novo introduzido por esses três merges nos 31 PDFs novos com evidência suficiente para reprodução.
Isso identifica lacunas anteriores; não prova que nunca tenha existido uma regressão em versões mais antigas.

Um Stone não preservou a evidência OCR da falha. A leitura do PDF confirma que é uma imagem e um trecho de extrato,
mas a causa financeira exata da recusa anterior não pode ser afirmada sem repetir o OCR.
A tentativa de repetição recebeu `AccessDeniedException` para `textract:StartDocumentTextDetection`.
Nenhuma política IAM foi alterada e nenhum OCR novo foi executado com sucesso.

## Representação dos 32 PDFs novos

| Representação | Quantidade | Implicação |
|---|---:|---|
| Texto nativo | 18 | Os caracteres existem; ainda é necessário interpretar layout, colunas, sinais, datas e tipo de relatório. |
| Imagens | 13 | Dependem de OCR. Um recibo Itaú estreito sobre A4 estava classificado como `unknown_no_text`. |
| Letras em contornos vetoriais | 1 | O Sicredi parece digital, mas não tem fontes/texto extraível. Precisa de extração visual/OCR. |

As métricas históricas registravam 18 nativos, 12 imagens, 1 vetorial e 1 desconhecido.
O classificador v3 passa a reconhecer o Itaú estreito como imagem: as margens brancas do A4 não mudam a natureza do conteúdo.
Isso não reduz a necessidade real de OCR; corrige a identificação da representação.

## Diagnóstico e tratamento por caso

### Sicoob — 10 resumos de Captação Remunerada/SISBR REM116

**Representação:** texto nativo. **Origem do problema:** tipo de relatório ainda não coberto; já acontecia na versão anterior.

São resumos mensais de aplicações, resgates, rendimentos, impostos e posições, sem datas individuais dos movimentos.
A ordem interna dos objetos PDF também não acompanha a ordem visual: algumas cifras aparecem muito antes de seus rótulos.
O parser genérico associava a data de emissão a uma cifra de imposto previsto e criava um crédito bancário inexistente.
Portanto, não era falta de OCR nem uma transação simplesmente ignorada.

O novo leitor preserva a associação visual entre campo e cifra, identifica o período do investimento e separa a emissão.
Cada arquivo produz **11 observações financeiras**, sem inventar eventos bancários. As duas equações conferidas são:

- Saldo bruto anterior + aplicações + rendimentos brutos − resgates líquidos − IR retido − IOF retido = saldo bruto final.
- Saldo bruto final − IR previsto − IOF previsto = saldo líquido final.

Os dez originais passaram nessas conferências. A prévia mostra os campos e agregados do período; CSV/Excel preservam os detalhes.
OFX permanece indisponível para esse resumo, pois não há datas individuais de transações.
Um resumo validado com posições deixa de receber `no_transactions` apenas por não representar movimentos bancários.

### Banco do Brasil — 2 extratos de conta corrente

**Representação:** texto nativo. **Origem:** variante do layout sem leitor financeiro suficiente; os erros já se reproduzem antes da #457.

O formato imprime `valor (+)` ou `valor (-)`, além de saldos diários na mesma coluna.
O leitor genérico perdia sinais em algumas linhas e separava incorretamente valores com milhar, chegando a confundir valor e saldo.
Um pagamento de R$ 1.000,00, por exemplo, havia sido convertido em −R$ 16,35.

Agora o sinal vem do marcador impresso; linhas de saldo são controles, não movimentos.
Datas, saldo anterior, cada saldo diário e saldo final são conferidos com Decimal.
Referências numéricas, contrapartes e descrições continuadas são preservadas.
Os originais mantêm **57 e 44 movimentos**, com sinais/valores corrigidos e **zero divergências de saldo**.

### XP Conta Digital — 1 variante diferente da PR #456

**Representação:** texto nativo. **Origem:** layout novo para a cobertura; não é a mesma estrutura “Conta Digital PJ” tratada na #456.

Este arquivo tem data/hora, valor e saldo por linha, em ordem decrescente.
Os 23 valores já estavam corretos, mas o arquivo era identificado como um layout Caixa e conferido na direção inadequada.
Isso produzia 22 avisos e limites inicial/final incorretos.

O leitor próprio identifica a XP pelo cabeçalho, reverte a ordem física completa — inclusive os movimentos do mesmo dia —
e confere todos os saldos e o saldo disponível impresso. Resultado: **23 movimentos, zero avisos de saldo**.
A origem física continua vinculada à página/linha original. O banco XP é mantido também no OFX.

### Banrisul — 1 extrato monoespaçado com OP/DOC/SIS

**Representação:** texto nativo. **Origem:** variante ainda não coberta; não precisava de OCR.

O dia aparece no primeiro lançamento de um grupo, enquanto mês/ano ficam no cabeçalho.
Os lançamentos seguintes herdam o dia. Há referências alfanuméricas e sinal negativo depois do valor.
O leitor antigo esperava outro formato Banrisul e não reconhecia nenhuma transação.

O novo leitor associa o contexto de data, preserva as referências e confere cada `SALDO NA DATA`, inclusive negativo.
Resultado no original: **87 movimentos, zero divergências de saldo**, com identificação Banrisul no OFX.

### Tabelas sem identificação e sem sinal — 3 arquivos

**Representação:** texto nativo. **Origem:** exportação incompleta para fins bancários; o comportamento incorreto era anterior.

Os arquivos contêm data, referência, descrição e cifra positiva. Não contêm banco, colunas de débito/crédito ou sinal explícito.
Pagamentos e PIX enviados estavam virando créditos; algumas transferências nem permitem determinar a direção pela descrição.
Escolher sinais apenas por palavras não recupera a informação retirada da exportação.

Agora esses três arquivos recebem uma mensagem específica solicitando o original com sinais/colunas.
**Não há conversão bancária confiável desses arquivos tal como foram enviados.** Isso evita uma falsa conversão bem-sucedida.

### Caixa — 8 imagens, com duas situações distintas

**Representação:** imagens; as quatro com cabeçalho também passaram por impressão virtual.
**Origem:** lacunas do leitor OCR/controles, somadas a cortes nas outras quatro imagens. Não é regressão das PRs recentes.

**Quatro páginas com cabeçalho:** as colunas estão presentes. O leitor genérico descartava a descrição de `SALDO DIA`,
mas mantinha a cifra zero como se fosse uma transação. Também havia trocas de separadores no OCR, por exemplo `102,109,34`.
O novo leitor associa as cinco colunas, verifica todas as transições e usa saldo diário apenas como controle.
Cada arquivo passa de **28 para 21 movimentos reais**, conservando totais e saldos. As divergências de saldo ficam em zero.
Permanecem **1, 3, 1 e 0 avisos**, respectivamente, para normalizações numéricas ou sinal de saldo confirmado pelo livro.
Esses avisos representam recuperação conferível, não dinheiro divergente.

**Quatro páginas sem cabeçalho:** a primeira data está parcialmente coberta ou cortada; em uma delas também faltam dígitos de saldos.
O leitor antigo omitia o primeiro movimento e calculava uma abertura a partir de uma linha posterior.
Agora a cobertura é conferida pelo número do documento de cada linha. Uma linha incompleta interrompe a conversão com a causa específica.
Não são inventadas datas nem descartados movimentos para fazer o arquivo parecer correto.

### Sicredi — 1 PDF vetorial, reenviado duas vezes

**Representação:** contornos vetoriais, sem fontes/texto; o OCR é necessário e esperado.
**Origem:** faltava o leitor por coordenadas para essa variante visual. A #452/#458 cobria colunas nativas, não esta representação.

O histórico `SICREDI DEBITO ...` descreve recebimentos de vendas em cartão de débito: as cifras estão na coluna crédito.
O leitor textual usava a palavra “débito” como direção e também tratava um cheque como crédito.
A leitura pelas coordenadas usa os cabeçalhos de débito/crédito/saldo, e não palavras do histórico.
Os **47 movimentos** agora fecham com os três saldos diários impressos.

O arquivo contém apenas a primeira página e anuncia continuação. O produto permite dividir documentos para OCR;
por isso o trecho legível é convertido com **aviso de trecho parcial**, sem exigir o trimestre inteiro.
O saldo calculado no fim é do trecho fornecido, não uma afirmação sobre o encerramento do trimestre.

### Stone — 4 PDFs em imagens, todos trechos de extratos maiores

**Origem:** mistura de limite do trecho e associação OCR de colunas, já presente no código anterior.
Faltarem páginas fora do trecho não torna seus movimentos internos inválidos. Uma lacuna entre páginas do mesmo arquivo, contudo,
impede conferir a sequência e recebe erro específico.

| Trecho | Resultado |
|---|---|
| Junho, páginas 10–19 de 34 | **177 movimentos**, sem divergência de saldo; aviso de trecho e de tarifa anterior fora do arquivo. |
| Junho, páginas 30–34 de 34 | **82 movimentos**, sem divergência de saldo; mesmos limites do início do trecho. |
| Julho, páginas 1–10 de 43 | **179 movimentos**, sem divergência de saldo; recuperação de tarifa/saldo/tipo permanece sinalizada. |
| Julho, páginas 11–20 de 43 | Falha histórica por ambiguidade; **revalidação pendente de permissão Textract**. Não foi declarada resolvida. |

Nos dois primeiros, o primeiro crédito tem sua tarifa na página anterior, fora do arquivo.
Comparar esse valor bruto com o saldo líquido do próximo par gerava um aviso falso. A conferência começa no próximo par completo,
preservando o crédito e o saldo impressos e explicando o limite do trecho.

No terceiro, um saldo foi usado como valor de uma tarifa porque o OCR juntou campos e não leu a célula do valor.
O novo leitor usa coordenadas das palavras mesmo quando linhas OCR juntam colunas e usa testemunhas de páginas adjacentes.
A tarifa é recuperada em **−R$ 0,07**, com saldo confirmado pelos vizinhos e **aviso explícito de recuperação**.
A recuperação exige duas relações independentes de livro; quando a testemunha é outro par tarifa/crédito, uma sexta linha confere esse par.
Dígitos parcialmente lidos e anotações sobre células financeiras continuam exigindo revisão, sem substituição arbitrária.

### Itaú — 1 recibo digitalizado estreito em 3 páginas

**Representação:** imagem, antes classificada como desconhecida porque o recibo não tem a proporção do papel A4.
**Origem:** variante com dia isolado e contexto mensal, somada a perdas de OCR no documento.

O leitor antigo só encontrou três linhas em que a descrição PIX continha uma data completa incidental.
O resultado ignorava o restante da tabela e ainda assim era exportável; os avisos do fallback não entravam corretamente nos contadores.

O leitor novo associa dia/mês/ano, coluna monetária e controles `SDO CTA/APL AUTOMATICAS`, mantendo contexto entre páginas.
O contrato é testado via HTTP com tabela legível. **O original auditado continua sem conversão integral confiável**:
há uma cifra sem associação suficiente à data/histórico na evidência OCR, e não é correto entregar apenas três movimentos.
Agora ele é interrompido com mensagem de associação de linha, até receber OCR/imagem que permita conferir todas as linhas.

### FinanceHub — 1 exportação nativa

**Origem:** aviso de layout genérico, não falha comprovada de dinheiro.
Os 120 movimentos já fechavam com os totais. O leitor agora reconhece a estrutura e exige que entradas, saídas,
saldo do período, abertura e fechamento fechem antes de tratar esse formato como validado.
Resultado no original: **120 movimentos, zero avisos de saldo**.

## Por que menos OCR ainda não significava menos erros

1. **Extrair caracteres e interpretar um documento são etapas diferentes.** Os 18 nativos não precisavam de OCR,
   mas incluíam resumos de investimentos tratados como conta corrente, layouts sem cobertura e exportações sem sinais.
2. **Impressão virtual não implica texto nativo.** Os Caixa são imagens e o Sicredi contém letras desenhadas como vetores.
3. **A qualidade da imagem continua limitando a informação disponível.** Um dia cortado ou um histórico perdido não é recuperado
   apenas por haver muitos números reconhecidos no restante da página.
4. **Havia subcontagem do OCR nas falhas.** A exceção de validação financeira perdia `textract_attempted`/`textract_used`
   mesmo depois de o serviço ter respondido. Agora esses campos indicam OCR executado, sem confundir validação com ausência de OCR.
5. **Havia avisos não refletidos nas métricas.** O fallback textual montava linhas com avisos, mas omitia as métricas canônicas.
   Agora os contadores e a recomendação de revisão acompanham essas linhas.
   A validação financeira também recebe motivos próprios (`financial_validation_failed` e `ocr_financial_ambiguity`),
   em vez de aparecer como falha genérica de processamento ou texto insuficiente.
6. **Alguns alertas eram de cobertura ou escopo, não de divergência monetária.** Modelo genérico, trecho de extrato e valor recuperado
   precisam ser distinguidos de saldo inconsistente. Não foram removidos avisos reais para melhorar artificialmente o indicador.

O bucket é uma seleção de falhas/avisos e modelos genéricos, não a totalidade das conversões.
**Não se pode calcular a taxa global de falha nem atribuir uma melhora/piora de produção apenas a este recorte.**
Uma comparação operacional precisa separar versão, representação, modelo, documentos únicos, recuperação conferida e motivo de recusa.
As mudanças nas métricas valem para novas execuções; registros históricos não são recalculados nesta PR.

## Resultado e limites de validação

- **23 dos 32 PDFs novos** foram reproduzidos com conversão semanticamente correta: dez resumos Sicoob e treze extratos/trechos.
- **8** recebem recusa específica por informação insuficiente: três tabelas sem direção, quatro Caixa com datas cortadas e um Itaú com associação OCR incompleta.
- **1 Stone** permanece sem revalidação do OCR por falta de permissão AWS. Isso não é contado como correção validada.
- O Itaú reenviado e já corrigido na #458 continua com **285 movimentos sem avisos**.
- Nas conversões bancárias reproduzidas com a correção, não restou divergência de saldo. Permanecem avisos legítimos de trecho e recuperação.

Os testes incluem negativos que alteram saldos/valores, perda de linha, campos incompletos, testemunhas inconsistentes,
origem em mais de uma página, downloads CSV/Excel/OFX, relatórios e processamento assíncrono com HTTP real.
As evidências OCR previamente salvas são reutilizadas localmente; isso verifica interpretação e integração, não a disponibilidade atual do serviço AWS.

Sem migração de banco, alteração de IAM, mudança de retenção ou chamada a modelos de IA.
Falhas futuras de validação OCR passam a guardar suas linhas/palavras no mesmo bucket privado e na retenção já configurada,
para permitir diagnosticar a causa sem depender de uma nova execução do serviço.

Valida??o registrada: su?te completa com **2242 aprovados, 1 ignorado e 2 xfail**; ap?s o ?ltimo refinamento da regra de posi??es, **25 testes focados aprovados**. Ruff, sintaxe JavaScript, UTF-8/mojibake e `git diff --check` passaram. Os dois avisos de deprecia??o do FastAPI j? existiam.
