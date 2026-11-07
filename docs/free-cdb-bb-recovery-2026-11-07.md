# Recuperação FREE: posições de CDB e sinais explícitos do BB

## Evidência e comportamento

A revisão dos registros FREE de 7 de novembro de 2026, até 10h15 de São Paulo,
encontrou cinco registros correspondentes a quatro PDFs distintos. O teste
interno `bb_cash_rende_facil_combined_v1` ficou fora desta análise.
Os documentos e as evidências de clientes permanecem apenas no diretório local
ignorado de auditoria; os testes versionados usam dados fictícios.

Os relatórios BTG contêm quatro posições de CDB, suas aquisições e totais.
Esses campos não são lançamentos bancários. A leitura especializada usa as
colunas e as coordenadas originais, tanto no PDF nativo quanto no OCR, e gera
observações de posição e lote no contrato existente `financial_events_v1`.
Não gera eventos de caixa, limites de saldo bancário ou OFX. CSV e Excel
preservam ativos, quantidades, preços, datas, impostos, valores e referências
às páginas e linhas originais. A prévia conta as posições de CDB.

Os controles verificam líquido contra bruto e impostos, totais impressos,
lotes contra posições, quantidades, preços e agregados da carteira e emissor.
Campos legíveis que não conciliam permanecem disponíveis com revisão explícita.
Um traço impresso na coluna de imposto é preservado como evidência de zero;
um campo de imposto ausente no OCR permanece desconhecido e exige revisão.
O traço na posição de conta corrente permanece `-`, sem inventar saldo zero.

Uma continuação legível pode herdar as colunas e o período explícito. Períodos
independentes preservam suas próprias posições e referências físicas, inclusive
com contadores de página reiniciados. Ativos sem associação, páginas posteriores
ilegíveis ou outras seções financeiras não suportadas impedem apresentar uma
extração parcial como completa. Definições na página de disclaimers não são
confundidas com tabelas de posição.

No BB, o OCR colocou um sinal explícito antes do valor: `(+) 150,00`.
A leitura aceita o sinal entre parênteses antes ou depois do número, sem mudar
os dígitos ou inferir o sinal. Células financeiras malformadas permanecem na
sua linha e causam ambiguidade explícita, sem juntar datas de movimentos vizinhos.
O documento recuperado contém 57 movimentos e 16 controles de saldo impressos.

## Validação

Os testes de regressão cobrem PDF nativo/OCR, sinais positivos e negativos,
campos ausentes, divergências, proveniência, continuação sem cabeçalho,
múltiplos períodos, lotes legítimos repetidos e evidência OCR duplicada na mesma
região de tinta. Também exercitam prévia e exportações por HTTP real no FREE
e no worker.

Resultado da validação local: 4.489 testes aprovados, 62 ignorados e dois
resultados esperados de falha (`xfail`), sem falhas inesperadas. As comparações
com a main `b96db5a` passaram em 90 documentos e 170 casos de revisão, sem
diferenças. Ruff, verificações de UTF-8/navegação e smoke de navegador FREE
nas três páginas, no celular e desktop, também passaram.

```powershell
.\backend\venv\Scripts\python.exe scripts/test_backend.py
.\backend\venv\Scripts\python.exe -m ruff check backend
.\backend\venv\Scripts\python.exe scripts/lint_frontend_text.py
.\backend\venv\Scripts\python.exe scripts/lint_frontend_navigation.py
node --check frontend/ofx-convert.js
.\backend\venv\Scripts\python.exe scripts/check_pdf_compatibility.py --base-ref origin/main --output-dir tmp/free-cdb-pdf-compatibility
.\backend\venv\Scripts\python.exe scripts/check_review_compatibility.py --base-ref origin/main --output-dir tmp/free-cdb-review-compatibility
```

O caminho público vigente é `POST /convert`; `POST /analyze` está desativado e
retorna 405. A validação HTTP inclui upload de CSV, `GET /report/{analysis_id}`,
identificador inexistente (404) e recusa de OFX para posições (400), inclusive
quando o cliente informa uma conta de destino.

## Produção e reversão

Não há migração, alteração de armazenamento, reenvio a clientes ou publicação
automática. O contrato financeiro e as rotas existentes são mantidos; os novos
tipos de observação usam os campos extensíveis já disponíveis. O frontend
anterior continua recebendo o mesmo contrato, embora a contagem especializada
de CDB requeira a versão nova. A API e o worker precisam receber a mesma versão
para compartilhar a correção. A reversão consiste em republicar a versão
anterior; os originais e as evidências não são alterados por esta mudança.

Após publicação, converter novamente os documentos e conferir quatro posições
nos BTG, revisão dos impostos não reconhecidos no OCR, 57 movimentos no BB e
os 16 controles conciliados. Não reprocessar ou substituir resultados históricos
automaticamente.
