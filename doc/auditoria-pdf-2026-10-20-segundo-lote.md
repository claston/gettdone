# PDF: segundo lote de 20/10/2026

## Recorte e diagnóstico

Este lote contém 12 capturas de 20/10/2026, das 13h53 às 15h30 de Brasília:
11 PDFs inéditos e um reenvio Itaú. Os três Bradesco são arquivos diferentes,
mas contêm trechos sobrepostos do mesmo extrato.

Os 11 inéditos reproduziram os mesmos defeitos na versão anterior às PRs
#456–#458 (`823e32f`), na main pós-#458 e na #459. Portanto, neste recorte,
são lacunas de cobertura anteriores às correções do dia. Isso não exclui
uma regressão em versões mais antigas. A implementação parte de `25219b0`,
com a #459 já mergeada.

As capturas registravam `APP_RELEASE=4d346f3` (#457). O valor é uma variável
do ambiente: isoladamente, não comprova qual código estava implantado.

| Representação | Capturas | Tratamento |
|---|---:|---|
| Texto nativo | 7 | Dois Banrisul, Santander, três BTG e um Banrisul com fonte sem mapeamento Unicode utilizável. |
| Letras em contornos vetoriais | 4 | Stone e três trechos Bradesco; requerem OCR apesar da aparência digital. |
| Mista, com texto utilizável | 1 | Reenvio Itaú já coberto. |

Os PDFs e os artefatos financeiros detalhados permanecem no cache privado de
auditoria. Os exemplos versionados são sintéticos e não contêm documentos
ou identificadores dos clientes.

## Resultado por grupo

### Banrisul BVP-PORTAL: dois extratos mensais nativos

O leiaute imprime operação, histórico, documento, valor e sistema. A data só
aparece no primeiro movimento de cada grupo; o sinal negativo fica depois
da cifra. O leitor genérico descartava os demais movimentos do dia e lia o
saldo inicial negativo como positivo.

- Agosto: **158 movimentos**, antes 21; saldo inicial −1.131,57 e final −158,93.
- Julho: **156 movimentos**, antes 23; saldo inicial −334,75 e final −1.131,57.

O leitor específico herda a data entre linhas e páginas, preserva o documento
como texto e confere os 21 e 23 saldos diários, respectivamente. O campo
`SALDO CALC. ENCARGO` é outra base de cálculo e não substitui o saldo da conta;
no original de julho, os dois valores finais são diferentes.

Antes, `balance_consistency_failed=0` não significava fechamento conferido:
os movimentos e controles necessários nem sequer tinham sido reconhecidos.

### Stone: variante Crédito/Débito e Lançamento

Este PDF tem nove páginas e letras desenhadas em vetores. O OCR preservado
contém **120 linhas datadas**, enquanto o leitor genérico retornava 11
movimentos de uma única página, com sinais e datas incorretos.

O novo leitor usa as colunas DATA, TIPO, LANÇAMENTO, VALOR, SALDO e CONTRAPARTE.
O tipo Crédito/Débito prevalece sobre palavras na descrição. Todas as páginas
financeiras precisam ter cada valor associado a uma data; a página final de
autenticação é reconhecida como rodapé, sem movimentos.

O documento apresenta 13 pares de crédito e tarifa com o mesmo saldo líquido
impresso. Esse saldo controla o par: não é o saldo imediatamente após o crédito
isolado. Ambos os valores são preservados; o saldo canônico fica no fechamento
do par, e a evidência original mantém os dois campos impressos.

Resultado: **120 movimentos**, saldo inicial 1.459,90, final 45,66 e nenhuma
divergência de saldo. Nenhuma cifra monetária foi substituída por uma diferença
de saldos.

### Bradesco Net Empresa: três fragmentos vetoriais

Os arquivos contêm as folhas 1–3, 3–5 e 5–7 de um documento com dez folhas.
Há páginas repetidas entre arquivos; somar os três resultados duplicaria dados.

O OCR leu as cifras, mas o leitor genérico confundia a coluna financeira com
palavras como `DEBITO` na descrição e perdia linhas sem data explícita. O leitor
específico usa documento, crédito, débito e saldo nas posições impressas,
mantém a data nas continuações e exige páginas consecutivas e cobertura dos
campos monetários, além do fechamento de cada saldo.

- Folhas **1–3**: **122 movimentos**, saldo inicial 5.529,99 e saldo ao fim do
  trecho 6.685,84. Todos os saldos conferem. Permanece um aviso de trecho parcial;
  esse saldo não representa o fechamento das dez folhas.
- Folhas **3–5** e **5–7**: **HTTP 400**, com orientação para incluir a primeira
  página. O início está sem cabeçalho e contexto de data de movimento; a folha 5
  também começa com uma descrição continuada da página anterior. Datas em
  complementos `REM:`/`DES:` não garantem a data bancária de lançamento.

Essas duas recusas são o tratamento correto dos arquivos recebidos. Não se
omitem as linhas iniciais nem se adivinham datas para produzir um OFX.

### BTG: três relatórios de posições de fundos

São relatórios de investimento com resumo de posições, quantidade e cotação de
cotas, provisões de impostos e lotes históricos de aquisição. Não são extratos
de movimentação de conta corrente. O leitor genérico criava 4, 12 e 12
transações bancárias a partir de posições e compras antigas.

O leitor nativo PDFium preserva os números e os campos que o extrator anterior
fragmentava, sem OCR. O contrato `financial_events_v1` registra:

- saldos bruto e líquido anteriores e finais;
- posição do fundo, cotas, cotação, IR, IOF e variação nominal;
- principal ajustado, saldos e impostos de cada lote, com sua data histórica;
- posição de conta corrente explicitamente impressa como zero;
- período do relatório, emissão e identificadores do produto/fundo.

Resultado: **25, 70 e 70 observações**, sem eventos bancários fictícios. As
posições, os totais, os impostos e a composição dos lotes são conferidos.
As cotas usam um limite de arredondamento calculado pela precisão impressa de
cada parcela e agregado; a tolerância monetária continua zero. As validações
registram esse limite explicitamente.

CSV e Excel ficam disponíveis. OFX retorna 400, pois o relatório não contém
eventos bancários. A prévia HTML mostra saldos finais e datas históricas dos
lotes; não apresenta aplicações/resgates iguais a zero quando esses agregados
não são informados. Relatórios com outra estrutura ou conta corrente não coberta
recebem validação específica em vez de cair no leitor genérico.

### Banrisul: fonte Type3 sem texto Unicode utilizável

O PDF tem quatro páginas visualmente legíveis, mas a fonte retorna milhares de
quadrados e caracteres sem significado nos extratores nativos. Apenas contar
caracteres classificava o arquivo como nativo utilizável e impedia o OCR.

A avaliação de qualidade passa a detectar concentração de caracteres de fonte
não mapeada por página. O classificador `pdf_representation_v4` preserva a
representação física nativa e marca `requires_visual_extraction=true`.

Foi possível executar **Textract síncrono nas quatro páginas originais**, sem
alterar IAM. Essa operação é diferente do início assíncrono que havia recebido
AccessDenied na auditoria anterior. A evidência foi mantida localmente.

O leitor visual Banrisul confere operação, documento, valor e saldo diário, com
herança de data inclusive entre páginas. Resultado: **158 movimentos**, saldo
inicial 0,00, final 40,47 e nenhuma divergência de saldo.

### Santander/Mister Contador e reenvio Itaú

O relatório Santander já tinha seus **52 valores e sinais corretos**. Recebia
aviso de leiaute genérico. Agora há identificação específica e cobertura de
todas as linhas datadas, preservando o formato monetário com ponto decimal e
vírgula de milhar. Como o documento não imprime saldos, não se inventam limites
inicial/final nem se declara fechamento contábil validado.

O reenvio Itaú continua com **531 movimentos e nenhuma divergência**, sem novo
OCR. É uma verificação de compatibilidade, não um novo defeito.

## Métricas e limites

As métricas internas da análise e os metadados permitidos da captura passam a
preservar `financial_controls_validated`, `printed_balance_checks`,
`balance_validation_status`, `statement_rows_expected`, `statement_rows_parsed`
e `statement_row_coverage_rate`. O contrato público existente não foi alterado.

`not_available` significa que o relatório não oferece saldo para conferir;
`validated` exige os controles previstos pelo leitor. A cobertura se refere às
linhas financeiras detectadas no arquivo recebido, não à totalidade de um mês
ou de um documento maior quando há aviso de trecho parcial.

Esses números são de uma amostra selecionada por falhas/avisos, não uma taxa de
erro da produção nem um benchmark de acurácia do provedor OCR.

## Validação e compatibilidade

- Testes sintéticos reproduzem sinais, datas herdadas, referências com zeros,
  continuações perto do topo da página, saldo compartilhado de crédito/tarifa,
  valores sem documento e posições de investimento.
- API HTTP real e worker validam os mesmos contratos e exportações.
- Os 12 originais são reprocessados localmente. OCR vetorial é reutilizado das
  evidências; o Banrisul com fonte ilegível usa a evidência real nova das quatro
  páginas. Isso não mede disponibilidade do serviço externo no momento do replay.
- Resultado observado: **10 conversões e duas recusas específicas**; CSV/Excel
  para os dez resultados, OFX para os sete extratos bancários e recusa OFX para
  os três relatórios de posições BTG.
- `/analyze` está desativado nesta versão e responde 405, como exige seu teste
  existente. A validação funcional usa `/convert`, `/report` e `/convert-report`.

Validação final local:

```text
backend\venv\Scripts\python.exe -m pytest backend/tests -q --tb=short --basetemp <Temp exclusivo>
2274 passed, 1 skipped, 2 xfailed, 2 warnings in 277.12s

cd pro; npm run test:run
Test Files 30 passed (30)
Tests 128 passed (128)

Ruff: All checks passed!
Frontend text lint passed.
Frontend navigation lint passed.
npm run lint, npm run typecheck, node --check, git diff --check: sem erros.
```

Os dois avisos Python são as depreciações existentes de `FastAPI.on_event`.
As 14 pastas exclusivas de pytest foram removidas após a validação; os caches
de originais e OCR foram preservados para reprodução.

Não há migração de banco de dados, alteração de retenção, IAM ou configuração
de produção, nem mudança no algoritmo de conciliação. Rollback: reverter os
commits desta PR e reimplantar; análises já salvas mantêm o formato existente.
