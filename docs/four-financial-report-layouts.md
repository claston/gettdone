# Relatórios Sicredi, fundos BB e pagamentos Stone

Os quatro formatos identificados na auditoria de 9 de novembro de 2026 usam o
contrato existente `financial_events_v1`. A identificação antecede os leitores
genéricos de conta corrente, tanto no PDF nativo quanto no OCR. Uma falha de
leitura nesses relatórios não pode retornar ao caminho que transforma saldos
ou posições em movimentos bancários.

| Formato | Representação preservada |
| --- | --- |
| Sicredi SICREDINVEST consolidado | Oito campos por mês: aplicações, resgates, rendimentos pagos, IRRF, IOF, rendimento do mês, provisionado acumulado e saldo atual; posição para saque e seus impostos. |
| Sicredi SICREDINVEST Automático matricial | Os mesmos campos mensais, produto, posição anterior e posição de saque, com as datas efetivamente impressas. |
| BB Investimentos Fundos Mensal | Posições, movimentos de investimento quando presentes, quantidades e saldo de cotas, cotação, resumo mensal e rentabilidade, associados a cada fundo. |
| Stone comprovante de pagamentos | Data, valor informado, modalidade do cartão, bandeira, situação, identificação e código de cada repasse. |

Os investimentos são observações, sem movimentos de caixa fictícios. O mês
`10/2026` permanece um mês; não recebe um dia inventado. Os pagamentos Stone
usam eventos sem efeito bancário projetado: “Débito” é modalidade de cartão,
e o status impresso não fornece um saldo bancário. CSV e Excel estão disponíveis;
OFX permanece indisponível, inclusive se o cliente informar uma conta de destino.

## Evidência e controles

As colunas e referências de página/linha provêm da evidência espacial original.
Continuações podem herdar cabeçalhos. Blocos independentes preservam sua conta,
produto ou fundo, mês e páginas físicas. Registros iguais em posições distintas
não são eliminados. Sobreposições, intervalos entre blocos e divergências dos
controles requerem revisão; não são corrigidos por ajuste de valor.

No Sicredi, os controles conferem saldo anterior, aplicações, resgates,
rendimentos pagos e impostos contra o saldo mensal; também conferem rendimentos
provisionados, totais e líquido para saque. No BB, posições e operações são
comparadas ao resumo de cada fundo, e o resumo é conferido por suas equações
impressas. O resultado não declara um saldo bancário global validado.

Datas e valores ilegíveis, colunas ambíguas e páginas financeiras posteriores
não reconhecidas impedem uma extração parcial apresentada como completa.
Caracteres sem mapeamento no identificador Stone são preservados como escapes
Unicode e exigem revisão, sem substituir o caractere por um hífen presumido.

## Conferência privada dos quatro originais

Os PDFs e exportações de clientes permanecem no diretório local ignorado de
auditoria. Nenhum original, nome, conta, identificador ou valor de cliente faz
parte das fixtures versionadas, que são inteiramente fictícias.

- Sicredi consolidado: sete meses, 17 observações e 30 controles aprovados.
- Sicredi matricial: um mês, oito observações e dez controles aprovados.
- BB: dois fundos, quatro posições e 30 observações ao incluir resumo,
  cotações e rentabilidades; 12 controles aprovados e nenhum evento de caixa.
- Stone: 32 repasses. O multiconjunto independente de datas e valores coincide
  integralmente, incluindo multiplicidades. Os 31 identificadores com glifos
  sem mapeamento ficam em revisão; a cobertura do documento foi conferida.

A leitura independente usa o texto original e compara os oito campos de cada
mês Sicredi, as datas e valores das quatro posições BB e as datas/valores dos
32 repasses Stone. Exportações CSV e Excel foram geradas localmente para os
quatro PDFs. Não houve reprocessamento nem alteração em produção.

## Validação automatizada

- Testes inicialmente vermelhos reproduziram a ausência de suporte nativo/OCR.
- Suíte completa do backend: **4.582 aprovados, 62 ignorados e dois xfail**, em
  350,34 segundos. O runner usou um processo conforme a memória disponível e
  removeu seu diretório temporário. Há dois avisos de depreciação existentes.
- Após os últimos ajustes de continuidade e cobertura: **64 testes aprovados**,
  incluindo upload público, worker, relatório, CSV, Excel e recusa de OFX por
  HTTP real para os quatro formatos em PDF nativo e OCR simulado.
- Comparação com `origin/main` (`a1de03d`): **90 documentos e 170 casos de
  revisão**, sem erros nem diferenças geométricas de plataforma.
- Suíte PRO: **350 testes aprovados em 61 arquivos**, com um worker para
  respeitar a memória local. A prévia mostra meses, fundos, valores e origem.
- Ruff, TypeScript, ESLint, sintaxe JavaScript, lints de texto UTF-8/navegação
  e `git diff --check` passaram.

O caminho vigente de conversão é `POST /convert`. A validação inclui um CSV
suportado, `GET /report/{analysis_id}`, relatório inexistente (404), OFX não
permitido (400) e a rota histórica `POST /analyze` desativada (405).

Comandos a partir da raiz do checkout, usando o Python do ambiente virtual:

```text
python scripts/test_backend.py
python -m pytest backend/tests/test_period_financial_reports.py backend/tests/test_period_financial_reports_http.py backend/tests/test_cdb_position_frontend.py
python scripts/check_pdf_compatibility.py --base-ref origin/main
python scripts/check_review_compatibility.py --base-ref origin/main
python -m ruff check backend
python scripts/lint_frontend_text.py
python scripts/lint_frontend_navigation.py
node --check frontend/ofx-convert.js
```

Os testes de OCR usam evidência sintética com coordenadas e a mesma entrada dos
leitores de produção; não houve nova chamada externa ao Textract. A conferência
dos quatro originais foi feita no caminho nativo, pois todos têm texto nativo.

## Compatibilidade e reversão

Não há migração nem alteração de schema persistente. Os novos tipos de
observação e campos usam a estrutura extensível já existente. Resultados
históricos permanecem inalterados. API e worker devem receber a mesma versão;
as prévias FREE e PRO preservam o mês e a identificação do investimento.

Reversão: reverter o commit e republicar API, worker e frontend. Não há backfill
nem transformação de dados a reverter. Depois da implantação, acompanhar os
quatro códigos de evidência, contagem de registros, controles com revisão,
downloads CSV/Excel e a recusa de OFX.
