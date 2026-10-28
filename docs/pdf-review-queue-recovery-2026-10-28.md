# Correções da fila de revisões de 28/10/2026

## Escopo e evidência

Esta alteração complementa a PR #505. O recorte auditado contém **67 tentativas de conversão, correspondentes a 59 PDFs únicos**, no filtro Hoje / Revisões / todos os modelos, até 18h42 de Brasília. A contagem de tentativas não equivale à quantidade de documentos.

A conferência usa os PDFs originais e a evidência de extração já obtidos na auditoria. Os hashes foram verificados. Não houve consulta ao banco de produção, leitura de credenciais de banco, alteração de histórico ou chamada paga de OCR. Os documentos reais e os relatórios detalhados permanecem em `local/`, fora do Git; os testes versionados usam dados fictícios.

## Comportamento corrigido

| Modelo / situação | Correção | Evidência de aceitação |
| --- | --- | --- |
| Saldo anterior em leitores genéricos | Retira controles de abertura das transações, preservando o texto de origem, metadados disponíveis e pontos de conferência já estabelecidos pelo leitor. Um saldo de caixa mais investimentos não vira, por suposição, saldo de caixa. | Exemplos antigos de Santander, Bradesco, Itaú, Neon e BNB verificam os movimentos sem somar a abertura ao volume. |
| Sinais explícitos | O sinal e as colunas financeiras prevalecem sobre palavras da descrição. | O recebível BNB “cartão débito”, impresso como crédito, continua positivo. |
| Stone e Sicoob moderno | Lê as colunas físicas e a ordem do extrato; associa os saldos diários ao encerramento do dia. | Stone mantém os dois débitos; Sicoob preserva 66 movimentos, com os saldos de abertura e encerramento conferidos. |
| Caixa com histórico enriquecido | Separa data de lançamento, data efetiva, valor e saldo; não importa `SALDO DIA`. | Os quatro documentos contêm 42, 48, 58 e 42 movimentos; os saldos impressos conferem. |
| Omie.CASH | Usa situação, data e as três colunas de valor, saldo e saldo previsto. Preserva títulos pendentes como eventos sem efeito de caixa. | Nos sete relatórios: 458 movimentos conciliados e 22 títulos pendentes. As validações dos saldos passam. O CSV e o Excel preservam os 480 eventos; o OFX contém somente os conciliados. |
| Cresol | Encerra a leitura dos lançamentos em `Totalizadores`, retomando somente em outro bloco de lançamentos. Limite e previsão de encargos não são transações. | Os dois documentos passam a conter 208 e 299 movimentos. |
| Bradesco mensal incompleto | Preserva os movimentos legíveis mesmo sem as últimas folhas e sem o total global. | Os fragmentos com folhas 17/19 e 11/13 produzem 661 e 412 movimentos, respectivamente, ambos com revisão por arquivo parcial. |
| Bradesco unificado digitalizado | Separa crédito, débito e saldo e mantém conta corrente e poupança em produtos distintos. Valida cada mês separadamente, sem recusar movimentos legíveis por falta de controles. Valores em posições e totais não viram lançamentos. | Os quatro documentos produzem 64, 12, 19 e 74 eventos; os totais de cada produto conferem. Testes adicionais cobrem vários meses, capa e controles ausentes. |
| Sofisa e paginação | Corrige o nome do banco, mantendo o identificador histórico do perfil. Datas após `REF:` e nomes de contrapartes não comprovam paginação. | Os quatro documentos Sofisa preservam seus valores. O Itaú de 49 páginas não recebe aviso falso por conter Mercado Pago nas contrapartes. Rodapés reais incompletos continuam exigindo revisão. |
| Banrisul mensal | Separa dia e histórico pelos WORDs originais, aceita referências alfabéticas e delimita corretamente a abertura da tabela. | Os quatro nativos preservam 64, 141, 110 e 164 movimentos. O digitalizado recupera 126, com os saldos impressos conferidos. |
| Itaú com cartões de lançamentos | Associa a data do bloco, cada descrição e seu valor físico, sem usar datas mencionadas no histórico. | No documento de 13 páginas, recupera 235 movimentos e confere 33 controles diários. |
| Itaú pagamentos | Une todas as linhas do beneficiário dentro da mesma linha física da tabela. | Preserva 579 e 643 pagamentos e os respectivos totais impressos. |
| Credisis | Separa a descrição da coluna monetária. O hífen no fim do histórico não transforma um crédito em débito. | 56 movimentos; totais de créditos, débitos e saldo final conferidos, sem divergência de saldo. |
| Santander consolidado básico | Usa a coluna de datas e os controles disponíveis, mesmo sem totais mensais de crédito e débito. | 90 movimentos; abertura e encerramento conferidos com o documento. Datas de compras mencionadas no histórico não substituem a data da coluna. |
| BB poupança | Usa a coluna Valor e preserva um lançamento cujo valor está no fim de uma página e a data/continuação na seguinte. | 79 créditos; soma, abertura e encerramento conferidos com o documento. A referência física de ambas as páginas é preservada. |
| Sicredi com OCR embutido defeituoso | Detecta dígitos e sinais corrompidos sobre uma imagem e exige leitura visual. Lê WORDs quando o OCR junta cabeçalhos, datas e valores. Saldo ilegível não impede preservar um movimento com valor legível para revisão. | A rota visual foi exercitada com OCR local gratuito. O resultado local continua em revisão quando há baixa confiança ou divergência; não é uma certificação dos valores que uma nova execução do Textract produzirá. |

## Revisões que permanecem necessárias

- Arquivos parciais, abertura inferida, sinais/valores ambíguos, baixa confiança de OCR e divergências financeiras continuam visíveis. Não se altera um valor para fechar um saldo.
- Referências de operação compartilhadas não autorizam eliminar movimentos repetidos legítimos.
- No PagBank de 16 páginas, os controles de dois dias divergem pelo mesmo valor em sentidos opostos, sem lançamento correspondente que autorize ajuste automático.
- No Caixa de duas páginas, os saldos positivos impressos no primeiro dia não fecham com os débitos nem com o crédito do dia seguinte. A inspeção da imagem confirmou que a divergência não deve ser “corrigida” invertendo sinais ou ordenando artificialmente as linhas.
- Um relatório Bradesco que contenha conta corrente **e** poupança preserva ambos no CSV/Excel. O OFX fica indisponível nesse relatório para evitar misturar contas; para esse formato, deve ser usado o extrato de um único produto.
- Os limites de OCR dos planos permanecem vigentes. Os testes verificam a recusa antes de chamar o gateway quando o limite de páginas é excedido.

## Compatibilidade e armazenamento

Não há alteração de esquema, migração ou backfill. O contrato JSON HTTP permanece idêntico ao snapshot existente. Os relatórios especializados usam o contrato `financial_events_v1` já disponível, com campos opcionais existentes e `ofx_profile` nulo. Os nomes novos de documentos e métricas são aditivos; o identificador do perfil Sofisa é preservado.

O histórico e os arquivos já gerados não são reescritos. As correções se aplicam a novas conversões ou reenvios. PDFs com camada OCR defeituosa precisam de nova extração visual, sujeita ao plano contratado. Não se usa a auditoria local como substituto da extração de produção.

## Validação

- Reprocessamento offline dos 59 PDFs / 67 tentativas: todos produziram resultado, preservando revisões justificadas. Conferência de CSV, Excel e da política OFX, incluindo datas, sinais, multiplicidade e separação de produtos/eventos pendentes.
- Regressão dos 27 PDFs das PRs anteriores: 7.745 movimentos, com conferência de CSV, Excel e OFX.
- `scripts/check_pdf_compatibility.py --base-ref f25ef5f19ba431a22461a99f16ed71af1c8f22de`: 51 casos fictícios, sem erros; alterações de resultados anteriores exigem motivo explícito associado à fonte.
- Testes com HTTP real: `POST /convert`, `GET /report/{analysis_id}`, downloads, worker, erro 404 e limites de OCR. O antigo `POST /analyze` continua retornando 405, conforme o contrato atual.
- Suíte completa Python, Ruff, suíte Pro, ESLint, TypeScript e verificações de texto/navegação devem passar antes da entrega da PR. Os resultados exatos ficam registrados em `How to test`.

## Implantação e rollback

Mesclar as dependências da PR #505 na ordem da pilha. Publicar o backend e depois o worker com a mesma revisão. Conferir os downloads Omie, Bradesco com dois produtos e BB poupança; reenviar o Sicredi que exige nova leitura visual. Monitorar revisões por divergência/baixa confiança e não exigir que todo documento fique “limpo”.

Rollback: reverter o commit desta PR e publicar novamente backend/worker. Não há reversão de banco nem remoção de dados; arquivos já gerados permanecem preservados.
