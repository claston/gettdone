# Extratos recebidos — OFX-62

O PRO reconhece o titular dos documentos e reúne conversões de lotes diferentes
no dashboard **Extratos recebidos**. Conversões permanece autossuficiente: upload,
processamento, revisão e download por lote não exigem organização nem as tabelas
novas. O Free e os contratos comerciais existentes permanecem compatíveis.

## Reconhecimento e confirmação

`statement_identity.py` aplica a mesma política ao texto nativo e ao texto já
obtido por OCR, sem nova chamada de OCR. Aceita metadados explícitos de titular e
rótulos documentais de titular/razão social com CPF/CNPJ; registra representação,
página e linha. CPF/CNPJ genérico de banco ou contraparte não é evidência de
titular. Números de conta preservam zeros. Identificadores conflitantes deixam o
documento pendente. A cobertura de rótulos é conservadora: não é reconhecimento
universal de titulares de todos os layouts.

O serviço reutiliza clientes pelo CPF/CNPJ normalizado dentro do proprietário.
Sem documento do titular, uma conta de origem conhecida e inequívoca pode
identificar um cliente existente; nomes semelhantes não geram vínculos.
Recebimentos concorrentes são serializados pela organização e são idempotentes
por arquivo. Contas de um mesmo PDF não duplicam o total do cliente.

Dados insuficientes aparecem em **Identificação pendente**, com evidência e link
para conferir o PDF. O formulário é contextual ao documento, com confirmação de
dados ou associação a cliente já identificado; não há cadastro livre na navegação.
Confirmações usam versão de vínculo; edições inline usam a versão temporal do
cliente. Inferências não substituem vínculos manuais já existentes. Períodos
legados são preservados ao confirmar a identificação.

O worker grava a organização depois de concluir a conversão. Se essa gravação
falhar, a mensagem é repetida e apenas a organização é retomada; o arquivo
concluído não é reconvertido nem consome cota novamente. O refresh explícito do
dashboard também permite recuperar metadados de trabalhos existentes, em blocos
de até 100 registros, sem reler PDFs. Resultados antigos sem evidência confiável
ficam pendentes; conta de destino de exportação não é usada como origem.

## Consulta, editor e retenção

APIs novas ficam em `/api/pro/receipts`: dashboard, refresh, confirmação por
documento, edição do cliente, workspace e exportação por cliente. Todas usam a
autorização existente; escritas exigem origem permitida e o cabeçalho PRO. Não
há nova concessão de degustação, acesso de equipe ou mudança de plano.

O dashboard consulta metadados, com paginação independente de clientes e
identificações. Mostra exatamente Extratos, Prontos e Precisam de atenção.
Esses três indicadores contam somente documentos disponíveis: no instante exato
da expiração, o arquivo deixa de compor os cards. Se todos expiraram, os cards
mostram 0 / 0 / 0. Os totais históricos e expirados de cada cliente e conta
continuam preservados, sem alteração do status técnico armazenado ou do TTL.
Processamento em andamento não aparece como Todos prontos. Expiração e situação
técnica são dimensões distintas, inclusive no histórico após o expurgo.

`/client-review/{client_id}/{job_id}` reutiliza o mesmo `WorkspaceScreen`, grid,
visualizador e autosave. Cada documento conserva seu `batch_id` original para
PDF, gravação, revisão e retry. A consulta é paginada por chave estável e retorna
uma lista plana, sem agrupamento por conta. A exportação ZIP seleciona somente
documentos ativos daquele cliente e verifica novamente propriedade e vencimento.

O vencimento vem do registro de origem, não da associação ao cliente. O histórico
nunca estende um prazo observado. Uploads de outros lotes, navegação e confirmação
não alteram a expiração dos anteriores. O editor filtra expirados, inclusive ao
reabrir seu cache e no limite de expiração de uma sessão já aberta. A política de
expurgo de PDFs e resultados continua existente; não há cópia de dados financeiros
nas tabelas de recebimento. Histórico já expurgado antes desta implantação não é
reconstruído por suposição.

## Migração, publicação e rollback

1. Aplicar `20261028_02`, posterior a `20261028_01`, no ambiente escolhido:
   `venv\Scripts\python.exe -m alembic upgrade head`, dentro de `backend`.
   A migração cria apenas `pro_received_statements`, seu índice e
   `pro_received_statement_accounts`; não modifica tabelas anteriores.
2. Publicar API e worker compatíveis, mantendo `PRO_ORGANIZATION_ENABLED` desligado
   até concluir a preparação. Habilitá-lo também no worker para registrar novos
   recebimentos. O refresh permite retomar registros disponíveis sem reconversão.
3. Publicar o frontend e validar uma identidade elegível, dois lotes do mesmo
   titular, uma identificação pendente, edição inline, PDF, autosave e ZIP.
4. Monitorar 5xx de `/api/pro/receipts`, falhas `conversion_worker_message_failed`,
   DLQ, latência de refresh e crescimento do histórico. A identificação não é
   requisito para a conversão concluir.

O rollback operacional desliga organização e reverte frontend/API/worker quando
necessário, **mantendo tabelas e vínculos**. Conversões continua acessível. O
downgrade técnico remove somente as duas tabelas novas e perde esse histórico;
foi validado em banco descartável e não deve ser usado após uso real sem backup
e estratégia de restauração. Não há backfill de PDFs, cópia de lançamentos,
mudança de quota ou DDL em tabelas grandes existentes. Downtime esperado: nenhum.

Clientes, Contas, Períodos e Trabalhos não têm entradas na navegação pública;
suas implementações, rotas e APIs permanecem. As páginas legadas redirecionam
para o dashboard por padrão. Somente builds internos de aceitação podem usar
`PRO_INTERNAL_LEGACY_ORGANIZATION_UI=true` para exercitar as telas preservadas;
isso não concede permissões no backend. A CI usa esse modo exclusivamente na
suíte de regressão de Trabalhos. Não habilitar em builds públicos.

## Evidências de validação

- `test_received_statements.py`: identidade, ambiguidade, correções, múltiplas
  contas, idempotência e expiração exata com dois lotes em 24h e 30d.
- `test_received_statements_http.py`: servidor HTTP real, PDF nativo pelo pipeline,
  relatório original, múltiplos lotes, ZIP restrito, 401/404/409, expurgo e cota.
- `test_received_statement_migration.py` e `test_pro_organization_postgres.py`:
  upgrade, runtime, compatibilidade de clientes/vínculos antigos e rollback.
- Vitest: hierarquia do dashboard, edição, pendência, modo cliente paginado,
  preservação do lote original e bloqueio de documento em cache após expiração.
- `scripts/smoke_pro_organization.py`: aceitação vigente da OFX-62 no Chromium,
  desktop/mobile, sem overflow. O cenário anterior permanece em `run_legacy`.
- `scripts/smoke_pro_idle_navigation.py`: retorno ao histórico de Conversões após
  inatividade, mantendo o orçamento de navegação e as edições salvas.

As evidências de navegador são sintéticas. A validação local não publica código
nem altera produção; a implantação e o canário do worker permanecem operações
separadas. `/analyze` continua desativado (405), como na main; o caminho suportado
de lote e `GET /report/{analysis_id}` foram exercitados por HTTP real.
