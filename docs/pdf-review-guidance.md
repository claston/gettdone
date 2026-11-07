# Orientação de revisão baseada em evidências

A orientação distingue três situações sem alterar movimentos, sinais, datas,
repetições, referências de origem ou regras de exportação:

| Categoria | Quando se aplica | Orientação |
| --- | --- | --- |
| `financial_discrepancy` | Há comparação de saldo divergente ou aviso explícito de diferença nos totais. | Mostrar a divergência e manter a conferência dos valores de referência. |
| `validation_incomplete` | Há recomendação de revisão, ambiguidade, cobertura não confirmada ou evidência desconhecida. | Explicar o limite dos controles disponíveis; confiança técnica reduzida não comprova erro financeiro. |
| `information` | A única observação específica é referência compartilhada, com controles impressos validados, cobertura completa e nenhum impedimento contextual. | Explicar que referência compartilhada não comprova duplicidade e preservar os movimentos. |

Controles parciais bem-sucedidos são descritos no seu alcance original. Uma cadeia
de saldos intermediários não confirma abertura, fechamento ou cobertura de todo o
período. OCR, lacunas, campos incertos, anexos que exigem revisão e avisos futuros
desconhecidos impedem reduzir uma orientação a informação.

## Seis PDFs auditados em 05/11/2026

As datas, os valores, os sinais e a multiplicidade dos 481 movimentos foram
conferidos independentemente. Isso não certifica cada descrição ou a identidade
do titular. Os arquivos privados não são incluídos no repositório.

| Caso | Quantidade | Interpretação da evidência capturada |
| --- | ---: | --- |
| Asaas | 115 | Confiança técnica média, sem controles automáticos de saldo registrados: validação incompleta. A conferência externa de saldo não é promovida artificialmente a uma validação do sistema. |
| Itaú | 17 | Mesma distinção entre ausência de validação suficiente e erro confirmado. |
| C6, primeiro arquivo | 12 | OCR sem validação financeira completa na captura original; manter cautela e explicar o motivo. |
| C6, segundo arquivo | 12 | Mesma política. Controles internos adicionais não certificam saldo global. |
| Caixa | 165 | Reconhecer as 164 verificações entre saldos sem divergência, sem afirmar cobertura integral. |
| Mercado Pago | 160 | Referências compartilhadas em 45 linhas; controles impressos e cobertura validados: informação específica, sem deduplicação. |

## Contrato e compatibilidade

`analysis.review_guidance` é opcional, com `category`, `title` e `message`.
É calculado na resposta pública e na leitura do resultado assíncrono, inclusive
para resultados legados que possuem métricas suficientes. Nenhuma migração ou
backfill é necessário. Clientes antigos ignoram o campo; clientes novos mantêm
a mensagem anterior quando o campo não está disponível.

As métricas de confiança, os códigos de aviso, os estados de revisão e as
transações persistidas não são reescritos. O PRO apresenta também a revisão do
documento quando a validação está incompleta, sem criar suspeitas em campos que
não possuem avisos. Referências compartilhadas recebem explicação específica nas
linhas. Divergências atuais prevalecem sobre mensagens informativas; o PRO não
exibe confirmação antiga sobre valores editados localmente.

A implementação é independente das correções de extração do PR #556. Este PR
melhora a interpretação dos avisos; não adiciona novas regras de leitura dos
extratos, nem declara resolvidas outras falhas da auditoria.

## Validação e rollback

Regressões de política em `test_pdf_review_guidance.py`; conversão, worker e
exportações em HTTP real em `test_pdf_review_guidance_http.py`; testes Free/PRO
de mensagens, divergências, resultados legados e edição. O smoke
`scripts/smoke_free_review_frontend.py` cobre as três categorias nas três páginas
Free, em desktop/mobile, incluindo restauração do resultado e ausência de
rolagem horizontal.

Executar a suíte completa com `python scripts/test_backend.py`, Ruff, lints de
texto/navegação, testes/lint/typecheck do PRO e os gates oficiais de compatibilidade
PDF e revisão. Usar o ambiente virtual e, no Windows, pytest fora do sandbox.

Após integrar a `main` no commit `f2fc6fb`, o CI de push comparou a branch com
`71aa1c5`, que ainda não continha Documento. A comparação da PR com a `main`
`e79cab7` já passou; o push falhou porque os vínculos de aprovação ainda
apontavam para a revisão anterior da evidência.

As 3.304 diferenças PDF e 3.642 de revisão foram conferidas integralmente,
incluindo caso, caminho, antes/depois e multiplicidade. São as mesmas adições
Documento já enumeradas. Apenas os vínculos foram atualizados para `71aa1c5`
e a árvore `f3cfe6d40ccc7600bc371212b6d9839d2813724c`; evidência e hashes estão
em [pdf-review-guidance-ci-revalidation.json](pdf-review-guidance-ci-revalidation.json).
Para reproduzir o push, executar ambos os gates com `--base-ref 71aa1c5`
e `--approvals docs/transaction-document-approvals.json`. Para validar a PR,
usar `--base-ref origin/main`. Essa correção de CI preserva os comparadores,
baselines, oráculos financeiros e código da aplicação.

Rollback: reverter o commit e republicar. O campo adicional pode permanecer em
payloads existentes; o código anterior o ignora. Movimentos, exportações e dados
do banco permanecem compatíveis, sem operação de recuperação de esquema.
