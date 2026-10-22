# Correção dos layouts da recuperação de 22/10/2026

Revisão da captura de 48 tentativas e 35 PDFs diferentes, disponível até 14h03
no horário de São Paulo. As amostras de produção ficam fora do Git. Todos os
testes usam estruturas e dados sintéticos. As correções iniciais e a nova
varredura são entregues juntas no PR #473.

## Estruturas e tratamento

| Instituição / estrutura | PDFs | Leitura | Tratamento |
| --- | ---: | --- | --- |
| SumUp, período com data e hora | 1 | Texto nativo | Preservar descrições e sinais. Saldo inicial calculado a partir do final exige revisão. |
| Santander, período agrupado impresso | 1 | Texto nativo | Retomar lançamentos depois do atendimento; 30 movimentos. |
| Santander, consolidado inteligente sem movimento em conta corrente | 1 | Texto nativo | Confirmar os dois saldos da conta corrente; manter investimentos fora do caixa. |
| Cresol, posição sem movimento | 1 | Texto nativo | Preservar o saldo e exportar zero lançamentos. |
| Cresol, consolidado descendente | 1 | Texto nativo | Separar depósito bloqueado do caixa e conferir saldos diários; 121 movimentos. |
| Conta Azul, relatório de extrato | 2 | Texto nativo | Preservar descrição/contraparte e totais; não atribuir ao emissor o banco das contas filtradas. |
| Banco do Brasil, SISBB com origem e C/D | 2 | Texto nativo | Identificar o banco pelo cabeçalho, preservar sinais e conferir saldos; depósito bloqueado não é caixa disponível. |
| Banco do Brasil, data dividida na paginação | 2 | Texto nativo | Reunir somente o dígito efetivamente impresso na página seguinte, com referências às duas páginas. |
| Banco do Brasil, tabela Dia/Lote/Documento | 1 | Texto nativo | Recuperar a ordem visual das colunas e separar depósito bloqueado. |
| Banco do Brasil, autoatendimento | 1 | OCR | Associar colunas por coordenadas, conferir os saldos e preservar aviso de revisão para C/D ausente em Pix identificado explicitamente. |
| Sicoob, SISBR descendente | 1 | OCR | Ordenar 32 lançamentos cronologicamente, validando C/D e saldos diários. |
| Sicredi, objetos de texto sobrepostos | 1 | Texto nativo | Não substituir valores por diferenças de saldo. A amostra tem divergências nos próprios saldos impressos e requer documento corrigido. |
| CrediSIS, extrato descendente | 1 | Texto nativo | Ler caixa em ordem cronológica, separar depósito bloqueado e conferir cada saldo disponível. |
| Caixa, navegador com Data Efetiva | 1 | Texto nativo | Usar a data da primeira coluna e preservar a data efetiva nos detalhes; 107 movimentos. |
| Caixa, informativo mensal de CDB | 1 | Texto com glifos ausentes | Identificar posição de investimento; impedir lançamentos artificiais com datas de aplicação/vencimento. Exportação desta estrutura de posições permanece indisponível. |
| InfinitePay, movimentações sem coluna de saldo | 3 | OCR de páginas rotacionadas | Preservar sinais, horários e descrições. Os intervalos 1–10, 11–20 e 21–23 pertencem a um relatório de 23 páginas e permanecem marcados como parciais. |
| PagBank, extrato financeiro | 5 | OCR | Preservar os eventos de Disponível, A receber e Bloqueado e os saldos diários no contrato financeiro; exportar CSV/Excel sem OFX misturando contas. |
| Tribanco, conta vinculada | 1 | Texto nativo | Herdar datas dentro dos grupos e recuperar 269 movimentos; saldos C/C e CDB são controles/posições. |
| Alelo, despesas corporativas | 1 | Texto nativo | Associar valores e descrições divididos em linhas; 7 despesas sem usar saldo atual fora do período. |
| Bradesco, Net Empresa | 2 | OCR | Separar período principal de Últimos Lançamentos e conferir totais. O trecho válido permanece parcial; o arquivo com margens/datas cortadas exige nova cópia. |
| Bradesco, mensal DIA/HISTÓRICO/N.DOCTO/VALOR | 1 | OCR | Conferir dia, sinal, páginas e saldos. A amostra não fecha e permanece rejeitada. |
| Banco Inter, saldo por transação | 2 | OCR | Aceitar saldo numérico sem símbolo monetário; exigir cabeçalho/período e controles diários. Um arquivo começa sem cabeçalho; o outro mantém divergência diária após associar as colunas. |
| Itaú, mensal com aplicações automáticas | 1 | Digitalização | Identificar a estrutura pela seção/produto e rejeitar paginação incompleta; o arquivo tem 3 das 6 páginas indicadas. |

## Diagnósticos e compatibilidade

Falhas dos leitores nativos preservam texto, layout reconhecido, quantidade de
páginas e ausência de OCR. Falhas posteriores ao recebimento do OCR preservam
linhas e coordenadas, inclusive no modo de tabelas. Um erro de validação não
deve aparecer como falha de execução do OCR.

O contrato da API expõe indicadores já calculados de documento parcial,
controles financeiros, cobertura de linhas e depósitos pendentes excluídos.
São campos adicionais com valores padrão. Não há migração, alteração de
documentos de produção ou reprocessamento automático de uploads.

O PagBank usa `financial_events_v1`, com tipo
`payment_account_financial_report`. Eventos de A receber e Bloqueado têm
`cash_effect=null`. Códigos repetidos não eliminam eventos diferentes da mesma
operação. Diferenças entre saldos diários aparecem nas validações sem modificar
os valores impressos. A ausência de saldo inicial impresso permanece explícita.

## Validação e reversão

Testes cobrem alterações de valores/sinais/datas, valores sem linha associada,
depósitos bloqueados, trechos parciais, corte de seção, relatórios com várias
contas, diagnósticos e upload/download por HTTP real. O fluxo público atual usa
`POST /convert`; `POST /analyze` responde 405.

Validação final: `2630 passed, 1 skipped, 2 xfailed, 8 warnings` em 123,07 s,
com a suíte completa do backend (`-n 4 --dist=worksteal`). Ruff, verificação de
texto UTF-8/mojibake e navegação do frontend passaram. Os oito avisos são da
API de ciclo de vida do FastAPI já usada pela aplicação.

O replay usa os PDFs nativos e o OCR já capturado. A amostra Itaú, cujo OCR não
estava salvo, foi inspecionada também com OCR local para confirmar a paginação.
A credencial da sessão não permite iniciar OCR assíncrono no Textract; a
validação do controle de páginas usa testes sintéticos e a inspeção local,
sem alegar novo processamento Textract desse arquivo.

Após publicar, acompanhar genéricos e falhas por layout, cobertura, divergências
e motivos de revisão. Digitalizações incompletas ou saldos incorretos na origem
exigem nova cópia. Rollback: reverter os commits do PR e publicar novamente;
não há alteração persistente a desfazer.
