# Estruturas restantes da recuperação de PDFs em 22/10/2026

Continuação do PR #473, a partir de `main` após seu merge. A última consulta ao
bucket foi encerrada às 18h36 de São Paulo: 62 capturas de recuperação e 46 PDFs
únicos no dia. As 14 tentativas adicionais continham 11 PDFs inéditos; dois
Cresol já estavam cobertos, além de um reenvio Tribanco. Este ajuste trata os
nove PDFs restantes, agrupados em oito estruturas.

As capturas registravam a versão anterior ao PR #473. O replay descrito abaixo
é local; não representa um reprocessamento ou uma validação após deploy.
Documentos, nomes, contas, identificadores e evidências de produção não integram
o repositório. Os testes usam exclusivamente estruturas e valores sintéticos.

| Estrutura | PDFs | Extração disponível | Resultado do replay |
|---|---:|---|---|
| Itaú Empresas, origem/valor e posições de aplicações | 1 | Texto nativo | 158 movimentos; 23 posições `SDO CTA/APL AUTOMATICAS` excluídas dos movimentos, sem usar o saldo combinado como controle de caixa. |
| Banco do Brasil, conta corrente + Rende Fácil | 1 | OCR capturado | 48 movimentos com C/D e complementos; saldos combinados não viram movimentos nem controles exclusivos de caixa. |
| Bradesco Net Empresa, crédito/débito/saldo | 1 | OCR capturado | 134 movimentos; abertura, saldos por linha e totais do período conferidos. Invest Fácil e Últimos Lançamentos ficam fora do período principal. |
| PagBank, relatório de vendas | 1 | Texto nativo | 65 eventos de vendas com bruto, taxa, líquido, status, horário, NSU e identificadores preservados; sem efeito de caixa presumido. |
| Sicredi, valor/saldo e lançamentos futuros | 1 | OCR capturado | 140 movimentos com todos os saldos conferidos; seção futura excluída. |
| BRB, histórico deslocado de data/valor | 2 | Texto nativo | 25 e 106 movimentos; histórico principal, documento e complemento associados por página. |
| Banco do Brasil, Bank Statement em inglês | 1 | Texto nativo | 34 movimentos, em lugar de 26; datas de outubro lidas como `MM/DD/YYYY`, totais de entradas/saídas conferidos. |
| Caixa, extratos via internet de períodos independentes | 1 | OCR capturado | 97 movimentos; saldos diários excluídos dos movimentos e controles conferidos separadamente em janeiro, fevereiro e abril. |

## Controles e limites

- O saldo inicial do BB em inglês e os saldos iniciais por período da Caixa
  são derivados, não impressos. A recomendação de revisão permanece explícita.
  A Caixa mantém checkpoints separados e sinaliza que os períodos reunidos não
  constituem uma sequência integral de movimentos.
- Itaú e BB/Rende Fácil preservam a indicação de saldo combinado não comparável.
  BRB não usa o saldo atual como saldo inicial do mês e exige correspondência
  de todos os históricos e valores. A ausência de controles permanece visível.
- O texto do alerta de saldo combinado abrange aplicações automáticas de ambos
  os bancos, sem afirmar que a posição do Itaú é Rende Fácil.
- O leitor Sicredi aproveita cabeçalhos de continuação apenas com deslocamento
  geométrico limitado e continuidade financeira completa. Separadores OCR
  seguem a recuperação conservadora existente. A ambiguidade `Z`/`2` no início
  de um saldo aceita somente evidência de palavra impressa, formato monetário
  estrito e fechamento integral do livro; mantém o aviso de saldo recuperado e
  a revisão recomendada. A evidência original nunca é modificada.
- No Bradesco, valores com separadores brasileiros ou americanos são aceitos
  somente na leitura das colunas e continuam sujeitos aos saldos e totais.
  Inclinação é compensada pela geometria; células ambíguas continuam rejeitadas.
  A presença de uma linha `TOTAL` não altera o contador de páginas.
- PagBank usa `financial_events_v1`, com `acquirer_sales_report`. Mesmo uma
  venda aprovada não confirma liquidação. CSV e Excel preservam os campos de
  origem e as validações de bruto menos taxa, líquido e totais diários. OFX não
  fica disponível. Divergências impressas são mantidas, nunca compensadas.
- Modos Textract `text` e `tables` passam pelos leitores específicos; não há
  fallback para a tabela genérica quando uma estrutura reconhecida falha.

## Validação e reversão

Os testes cobrem datas inválidas/fora do período, contagens e totais incorretos,
históricos ausentes, sinais ausentes/contraditórios, fontes manuscritas,
continuações sem cabeçalho, deslocamento e inclinação de colunas, paginação,
períodos independentes e valores sem associação. Há upload e download por HTTP
real, leitura de relatórios, CSV/XLSX/OFX compatíveis, recusa de OFX para vendas,
controle negativo e compatibilidade CSV. A rota pública atual de upload é
`POST /convert`; `POST /analyze` retorna 405.

Validação final: `2673 passed, 1 skipped, 2 xfailed, 8 warnings` em 131,68 s,
com `pytest backend/tests -q -n 4 --dist=loadfile`. Ruff e verificações de
UTF-8/mojibake e navegação passaram. Os avisos são da API `on_event` já usada
pelo FastAPI no projeto. Os dez testes HTTP passaram; as 35 amostras anteriores
mantiveram as contagens das 28 conversões e as sete rejeições protegidas. O
relatório de vendas preservou 65 eventos e passou nas 92 validações; há uma
venda pendente e nenhum evento com liquidação presumida.

Não há migração, alteração dos documentos do bucket, nova chamada de OCR pago
ou alteração de reconciliação. O replay usa texto nativo e OCR já capturado.
Após deploy, conferir layouts, contagens, divergências e indicações de revisão.
A reversão consiste em reverter o commit deste PR e publicar a versão anterior.
