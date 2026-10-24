# Organização manual do PRO — primeira iteração da OFX-24

A primeira entrega cadastra e edita clientes, contas e períodos livres e permite
vincular individualmente as conversões de lotes PRO já existentes. Clientes e
períodos são dimensões independentes: um arquivo pode continuar sem cliente,
participar de vários períodos e ser encontrado por filtros combinados.

As páginas `/clients` e `/periods` passam a oferecer os cadastros. `/conversions`
preserva o upload e acrescenta a lista de arquivos individuais, com a opção
“Sem cliente”, paginação e acesso à revisão existente.

## Escopo desta entrega

- CPF/CNPJ e nome fantasia são opcionais; a normalização do identificador serve
  para impedir duplicação do cadastro dentro do mesmo proprietário. Não valida
  dígitos verificadores nem faz consulta externa.
- Contas pertencem a clientes. Vincular somente um período não exige cliente
  ou conta. Contas preservam os números como texto, inclusive zeros iniciais.
- Períodos usam datas inicial/final e podem atravessar meses. Relacionar um
  arquivo a um período não recorta o arquivo nem altera seu relatório.
- A listagem carrega somente metadados dos arquivos, sem resultados financeiros,
  PDFs ou referências de armazenamento. Extração/sugestão de datas permanece na
  etapa seguinte; ausência de vínculo é apresentada como “Período a confirmar”.
- Não há somatório financeiro entre períodos, portanto esta entrega não duplica
  lançamentos em totais. Recortes e consolidação são uma etapa posterior.
- Associação/desassociação usa uma versão esperada. Edições concorrentes
  retornam HTTP 409 e não sobrescrevem silenciosamente uma organização recente.
- Vincular arquivos não enfileira jobs, não recalcula quota e não altera o
  proprietário ou o prazo de disponibilidade dos documentos.
- Registros expirados ainda presentes no repositório de lotes mostram sua
  expiração e não oferecem link de revisão. Não há extensão de retenção.
- A paginação percorre arquivos por data/ID; os filtros são aplicados aos arquivos
  da página. Uma página sem correspondências pode ter uma próxima página, o que
  a interface informa explicitamente.

Sugestões automáticas de titular/cliente/datas, organização em massa, fechamento
de períodos, inclusão do histórico legado sem lote e colaboração de equipe
permanecem fora desta primeira iteração. As sugestões continuam previstas na
OFX-24; convites, atribuição e permissões de membros estão na OFX-30.

## Acesso e preparação para equipes

Cada proprietário autenticado tem uma organização privada criada no primeiro
cadastro/vínculo. A associação de organização não é aceita do frontend. Não
há compartilhamento automático de arquivos entre usuários.

O piloto exige identidade autenticada e o entitlement de acesso ao PRO já usado
para lotes. Não usa nomes de planos para conceder acesso. A matriz comercial
definitiva do Pro Escritório deve ser alinhada à OFX-20 antes da liberação geral.
O flag operacional descrito abaixo controla o piloto e não substitui o
entitlement. O isolamento por proprietário continua obrigatório mesmo no piloto.

## Migração e rollout

1. Aplicar a migration aditiva `20261024_01` após `20261012_01`, com diretório
   de trabalho `backend`: `venv\Scripts\python.exe -m alembic upgrade head`.
   No deploy Linux, usar o comando equivalente no Python do ambiente.
   Usar exclusivamente a
   configuração do ambiente de implantação escolhido.
2. A migration cria seis tabelas (`pro_organizations`, `pro_clients`,
   `pro_accounts`, `pro_periods`, `pro_conversion_contexts` e
   `pro_conversion_periods`) e índices nas associações. Não modifica as tabelas
   existentes, não faz backfill nem transfere propriedade ou contratos.
3. Publicar o backend. Em produção a funcionalidade fica desligada por padrão:
   habilitar `PRO_ORGANIZATION_ENABLED=true` somente depois da migração e da
   validação do acesso dos usuários do piloto. Em desenvolvimento o padrão é
   habilitado. SQLite local cria as tabelas no bootstrap existente.
4. Publicar o frontend PRO, cujos rewrites same-origin incluem apenas os novos
   caminhos de organização necessários. Não é necessário publicar o worker.
5. Confirmar com uma conta elegível: criar período de 20/09 a 04/10 sem cliente;
   cadastrar cliente/conta; organizar dois arquivos do mesmo lote de formas
   diferentes; combinar “Sem cliente” com período; recarregar e abrir revisão.
   Comparar cota e quantidade de jobs antes/depois: devem permanecer iguais.
6. Confirmar 401 sem sessão, 403 para Free, 403 sem origem/header de escrita,
   404 para arquivo ou cadastro de outro proprietário e 409 para versão antiga.

As tabelas novas não são exigidas pelo startup global do código antigo/worker.
Isso permite aplicar a migration antes do rollout sem interromper a produção.
Com o piloto desligado, as novas APIs retornam indisponibilidade e a conversão
existente continua independente desse armazenamento.

## Rollback e impacto operacional

Rollback preferido: desligar `PRO_ORGANIZATION_ENABLED`, reverter o frontend e,
se necessário, o backend, mantendo as tabelas. Os dados de organização ficam
preservados e o fluxo anterior de conversão continua compatível.

O downgrade técnico para `20261012_01` remove apenas as seis tabelas novas e
**perde seus cadastros e vínculos**. Ele é validado em banco descartável, mas não
deve ser usado como rollback operacional após uso real sem exportar/guardar
backup desses dados e definir sua restauração. A migração não remove usuários,
conversões, arquivos ou consumo existente.

Não há backfill, alteração de tipo, rename ou criação de índice em tabela grande
existente. São DDLs curtos sobre tabelas novas; não há downtime planejado.
Monitorar erros 5xx dos caminhos `/api/pro/organization`, latência da listagem,
conflitos 409 e crescimento das tabelas novas durante o piloto. O teste de
upgrade/rollback do CI é repetível e usa um schema exclusivo por execução.

## Validação

- `test_pro_organization.py`: persistência, período independente, identificação,
  conta incompatível, conflitos de versão e cota inalterada.
- `test_pro_organization_http.py`: servidor HTTP real, autenticação/entitlement,
  origem/header, filtros independentes, associação e isolamento entre usuários.
- `test_pro_organization_migration.py`: DDL aditivo e constraints executadas em
  SQLite, upgrade/downgrade e rejeição de schema inválido.
- `test_pro_organization_postgres.py`: CI com PostgreSQL 16 descartável; aplica
  toda a cadeia Alembic, usa o serviço real, faz downgrade preservando usuários
  e reexecuta upgrade. Exige `PRO_ORGANIZATION_TEST_DATABASE_URL`, aceito somente
  em localhost/127.0.0.1 e no banco `ofx24_test`; nunca usa a URL de produção.
- `test_pro_organization_source_listing.py`: paginação por arquivo com isolamento
  por proprietário e consulta sem payload financeiro ou referência de armazenamento.
- Vitest: cadastros, períodos entre meses, filtros independentes, arquivos sem
  cliente, gravação do vínculo e apresentação de falha/conflito sem perder a seleção.

`/analyze` permanece desativado (HTTP 405), conforme a main. Os relatórios e o
fluxo público ativo continuam cobertos pela suíte de compatibilidade existente.
