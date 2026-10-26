# Organização de conversões do PRO — OFX-24

## Segunda iteração: Conversões independente e organização assistida

`/conversions` reúne upload, **Lotes** e **Todos os arquivos**. Lotes é a visão
inicial; IDs, estados e links de revisão existentes são reutilizados. O Início
mostra até cinco lotes recentes e o atalho para o histórico completo. Alternar
as visões mantém filtros e paginação; a revisão tem retorno para a lista com
seus filtros. A URL preserva o contexto para recarga e compartilhamento dentro
da autorização do proprietário.

### APIs e permissões

- `GET /api/pro/conversions` lista metadados sem depender de cadastros, tabelas
  ou serviços de organização. Exige sessão autenticada com permissão de lotes.
- `include_organization=true` acrescenta os vínculos quando o piloto está
  habilitado. Os filtros `client_id`, `account_id`, `period_id` e `unassigned`
  exigem essa opção e organização disponível; caso contrário retornam 403.
- A interface carrega a lista básica primeiro. Se os cadastros ou a consulta
  organizada falharem, mantém os arquivos/revisão acessíveis sem os filtros.
- A API antiga `/api/pro/organization/conversions` permanece compatível.
- Os filtros agora preenchem a página com correspondências, percorrendo blocos
  de até 100 metadados e buscando um resultado adicional para o próximo cursor.
  Uma busca esparsa pode percorrer várias consultas; memória permanece limitada,
  mas a latência pode crescer com históricos grandes. Não busca PDFs ou payloads
  financeiros para a listagem. Não é um novo índice SQL de pesquisa.
- A política comercial existente de acesso ao PRO foi preservada. Não há nova
  matriz de planos, acesso de equipe ou grant de degustação nesta entrega;
  a separação comercial definitiva continua na OFX-20/OFX-30.

### Organização em massa

Selecione arquivos da página e escolha manter/remover/substituir cliente e
conta, ou adicionar/remover um período. Adicionar um período preserva os demais.
A operação não significa atribuir todo o lote ao mesmo cliente.

`POST /api/pro/organization/contexts/bulk` recebe até 50 itens, cada um com
`batch_id`, `job_id` e `context` (cliente/conta/períodos/`expected_version`).
Verifica a propriedade de **todos** os arquivos antes de escrever. IDs repetidos
são rejeitados. A gravação é independente por arquivo: resposta 200 contém
`items` com status 200/400/404/409 e contexto ou erro. Não há atomicidade entre
arquivos. Conflitos não sobrescrevem vínculos; os itens com erro continuam
selecionados e a interface pede atualização/conferência antes da nova tentativa.
Autores, controle de versão e compatibilidade de conta/cliente são preservados.

### Evidências e sugestões

`GET /api/pro/organization/batches/{batch_id}/jobs/{job_id}/suggestions` usa
somente dados do arquivo autorizado e o catálogo do proprietário. Arquivo
expirado retorna 410. Buscar ou usar sugestões não grava vínculos.

O worker passa a guardar `organization_source` no JSON de resultado ao concluir
a conversão, antes das configurações de exportação. Esse snapshot preserva:

- identidade explicitamente identificada como `owner_name`/`owner_tax_id` nos
  metadados documentais existentes; não examina CPF/CNPJ genérico do banco ou
  de contrapartes e não faz consulta externa;
- banco/agência/conta de origem extraídos, com comparação exata normalizada e
  preservação de zeros; conta de destino posterior não altera essa evidência;
- intervalo documental quando os metadados o fornecem; caso contrário, datas
  observadas nos lançamentos como sugestão de trabalho, sem provar cobertura.

Não foi adicionada extração universal de titular em todos os layouts. Sem
evidência, o usuário organiza manualmente. Nomes ambíguos e contas repetidas
são sinalizados; documento do titular divergente não produz correspondência
por nome. Uma sugestão nunca sobrescreve silenciosamente um vínculo confirmado.

Resultados antigos, sem snapshot, usam apenas metadados explícitos de titular
e datas dos movimentos. Seus campos de conta podem já ter sido usados como
destino de exportação e não são tratados como evidência de origem. Não há
backfill, reprocessamento ou consumo para produzir sugestões.

As datas sugeridas podem ser ajustadas para criar/selecionar um período de
trabalho. Essa ação cadastra o período explicitamente; **Salvar organização**
confirma o vínculo. Datas ausentes não recebem mês presumido. Período de
trabalho e período documental aparecem separados. Os vínculos continuam sem
recorte financeiro ou soma de lançamentos.

### Publicação e rollback da segunda iteração

Não há nova migration. A organização continua usando `20261024_01` e o flag
`PRO_ORGANIZATION_ENABLED`; o conversor básico independe deles.

Publicar API antes do frontend, pois a nova UI usa `/api/pro/conversions`.
Publicar o worker atualizado para capturar origem nas novas conversões;
workers anteriores e resultados sem snapshot continuam compatíveis com a API.
O campo adicional segue o mesmo armazenamento/TTL do resultado, sem novos
arquivos, alteração de cota ou ampliação de retenção.

Para rollback, reverter frontend antes da API e, se necessário, worker. Manter
tabelas e vínculos. Desligar organização desabilita cadastros/sugestões/escritas,
mas mantém a listagem básica. Não executar downgrade como rollback operacional.

Validação adicional: `test_pro_organization_v2.py` cobre HTTP real, isolamento,
conflitos em massa, busca esparsa e operação independente. O teste do worker
confere preservação do snapshot após edição do destino. O cenário de navegador
`scripts/smoke_pro_organization.py` cobre desktop/mobile, seleção em massa,
conflito parcial, sugestão editável, retorno da revisão e organização desligada.

Fechamento com cobertura/ressalvas (OFX-61), degustação (OFX-26), equipes e
conciliação fiscal seguem como incrementos próprios.

## Primeira iteração: base manual

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
- A paginação percorre arquivos por data/ID. A segunda iteração passou a buscar
  correspondências antes de devolver a página, conforme descrito acima.

Sugestões e organização em massa foram acrescentadas na segunda iteração.
Fechamento de períodos, inclusão do histórico legado sem lote e colaboração
de equipe permanecem fora desta entrega; convites, atribuição e permissões de
membros estão na OFX-30.

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
