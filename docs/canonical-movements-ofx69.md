# OFX-69 — Autoridade financeira PostgreSQL

## Escopo e dependências

Implementação da fase 2 da alternativa B enxuta, sobre `7195a11` de `main`.
O PR #535 da OFX-68 foi integrado em 02/11/2026 às 04:19 UTC. A consulta do
GitHub retornou `MERGED`, mas nenhuma review formal na coleção `reviews`.
`ReviewCommandService`, o gate Free da OFX-67 e o gate de revisão da OFX-68
estão nessa base. Foram lidas OFX-64/66/67/68/69/70 e o parecer integral da
OFX-64. O recurso de notas compartilhadas retornou `Tool get_agent_context not found`.

Esta fase entrega schema, repositório transacional, adapters internos de adoção,
revisão, exportação e expurgo. **Não conecta esses adapters ao composition root
produtivo**, não muda a UI e não habilita nenhuma coorte. A integração completa
dos endpoints PRO/worker, permissões comerciais e rollout é a OFX-70. Os testes
instalam o adapter explicitamente no endpoint HTTP existente. O conversor Free
continua pelo caminho legado, sem consulta ao novo repositório.

## Incremento A: schema e identidade

Migration `20261102_01`, posterior a `20261101_01`. Não faz backfill, não altera
colunas existentes e não recalcula prazos de documentos.

| Estrutura | Responsabilidade |
| --- | --- |
| `canonical_analyses` | Marcador persistente `postgres_v1`, organização/cliente/job/análise, moeda explícita, versão agregada, metadados, baseline inicial, hash de ingestão, projeção compatível e prazo efetivo. |
| `bank_movements` | UUID interno aleatório, alias `transaction_id` existente, data `DATE`, valor `NUMERIC(18,2)`, moeda, descrição, estado de revisão, exclusão lógica, versão, ordem, cliente/conta opcionais, baseline e proveniência. |
| `movement_revisions` | Antes/depois, estado de revisão, autor, canal, instante do banco, operação e versão do movimento. Unicidade movimento/versão. |
| `movement_operations` | Recibo durável por análise/operação, hash do comando, autor/canal, resposta original, versão e alteração de metadados. Inclui alterações de saldo/configuração sem mudança de linha. |
| `movement_artifacts` | Manifesto imutável por análise/versão, chaves de objetos e SHA-256. |
| `canonical_artifact_cleanup` | Fila de exclusão de objetos após remoção/cascata da análise. Guarda somente o ID opaco e instante de enfileiramento; nenhum valor ou descrição financeira. |

FKs compostas restringem organização/cliente/conta. Índices cobrem expiração e
consultas por organização/cliente ou conta/data/ID. Não há unicidade por data,
valor ou descrição: repetições legítimas permanecem. FITID continua sendo
produzido pelo exportador existente, não pelo UUID canônico.

O baseline é o resultado normalizado originalmente recebido, não uma alegação
de texto bruto do PDF. Movimentos inseridos manualmente têm baseline nulo e
proveniência manual; não recebem eventos de extração inventados. A adoção não
cria revisões retrospectivas. Triggers protegem baseline e histórico de updates
e deletes diretos; a cascata de expurgo elimina os dados retidos.

Todas as associações de conta de catálogo começam pendentes nesta fase. A
evidência `statement_account`, bloco, página, linha e regiões permanece por
movimento. **O `account_id` do contexto documental nunca é distribuído pelas
linhas**, inclusive em PDFs multiconta. A confirmação/reassociação de conta
requer um futuro comando de escopo versionado; o writer documental antigo é
bloqueado quando tenta mudar cliente/conta de uma análise canônica.

Dinheiro passa por `Decimal` antes do SQL. NaN, infinito, fração inferior a um
centavo e valores que não atravessam exatamente a interface float do exportador
legado são recusados. Não há arredondamento silencioso para caber no schema.

## Incremento B: comando, CAS, recibo e auditoria

`PostgresMovementRepository` usa psycopg e o PostgreSQL existente. Não tem
bootstrap automático de schema, segundo banco ou fila de eventos financeiros.

Na transação de comando:

1. Bloqueia os registros de origem e verifica usuário ativo, organização do
   proprietário, identidade do job/lote, análise, cliente e prazos do job/lote.
2. Bloqueia o registro canônico e revalida seu prazo/escopo.
3. Busca o recibo pela identidade da operação. Mesmo conteúdo/autor retorna a
   resposta originalmente confirmada, mesmo se já houver uma versão posterior.
   Conteúdo diferente gera `AnalysisEditConflictError`, mapeado a HTTP 409.
4. Confere a versão agregada esperada e, quando fornecidas, versões individuais.
   O adapter antigo também aceita `expected_updated_at`, exigindo uma precondição;
   timestamps são monotônicos, mas a atualização SQL usa versão inteira.
5. Reutiliza as regras do `ReviewCommandService` da OFX-68 sobre snapshot destacado.
   Não modifica parsers, YAML, OCR, ordem física ou política de FITID.
6. Atualiza movimentos com `WHERE id = ... AND version = ...`, avança versões e
   insere revisões. Alteração financeira invalida sugestões heurísticas existentes
   no documento e confirmação humana; anotação isolada conserva finanças.
   Confirmação de revisão também é comando versionado/idempotente.
7. Avança versão agregada, atualiza projeção do job na mesma transação e grava
   recibo. A ausência de manifesto para a nova versão invalida downloads antigos.
8. Revalida expiração usando `clock_timestamp()` imediatamente antes do commit.
   O tempo não fica congelado no começo da transação.

Uma falha antes do commit desfaz movimentos, versões, revisões, recibo e projeção.
Uma falha depois do commit não desfaz a correção. O retry recupera o recibo.
Os locks são PostgreSQL, portanto valem entre processos e instâncias distintas.
O primeiro incremento usa CAS por documento: edições em linhas distintas do
mesmo documento podem conflitar. Não há merge financeiro automático.

O objeto `MovementScope` deve ser construído no servidor a partir da identidade
autenticada, entitlement PRO vigente e job de origem. Não é DTO público. O banco
revalida proprietário, cliente e documento, mas **não substitui o guard comercial
do composition root**. Não existe grant novo de trial nem permissão de equipe.

## Incremento C: única autoridade, adoção e derivados

Inventário dos writers, complementando `review-command-service-ofx68.md`:

| Caminho existente | Tratamento nesta fase |
| --- | --- |
| HTML Free e PRO → `POST /convert-edits` → local/S3 | Free preservado; contrato canônico exercitado por `CanonicalReviewAdapter`; barreira recusa o adapter legado no documento adotado. |
| `GET /convert-report` com overrides | Legado preservado; documento cercado não permite mutação pelo GET. Configuração canônica exige comando versionado. |
| `mark_reviewed` e `update_analysis` do repository de jobs | Trigger recusa projeção divergente; confirmação canônica usa o mesmo comando/CAS e recibo. |
| Worker/inline → `persist_conversion_result` → `save_analysis` | Criação inicial preservada; adoção interna ocorre somente depois da extração, antes da exposição ao editor. |
| Organização/recebimentos → contexto cliente/conta | Schema bloqueia reassociação legada de documento adotado, inclusive criação tardia de contexto quando cliente está pendente. |
| Reabertura por lote/cliente, trabalhos e fechamentos | Podem consumir a projeção transacional do job; a confirmação documental antiga é retirada no commit financeiro. Integração de rotas/caches é gate da OFX-70. |
| Retry/reconversão | Não migra nem reprocessa um documento canônico; análise nova tem identidade/origem própria. |

As linhas financeiras são lidas de `bank_movements`. Os metadados canônicos não
guardam outra lista editável de movimentos. O JSON do job é projeção gerada na
mesma transação; um trigger recusa modificações divergentes por writers antigos.
`structured_result` e IDs posicionais antigos permanecem formatos derivados;
`movement_ids` e `movement_versions` associam os aliases estáveis aos registros
SQL. Nenhuma entidade financeira usa a posição visual como chave.

`CanonicalRollout` nasce desabilitado. Para adoção, exige simultaneamente flag,
organização explicitamente selecionada, produto `pro` e análise nova. Free,
trial, organização fora da lista e migração de análise antiga são recusados.
Desligar essa configuração impede novas adoções; não troca a autoridade dos
documentos já canônicos.

`CanonicalIngestionService` é chamado apenas antes de expor uma análise nova a
editores. Autoriza a origem e grava primeiro `canonical_pending` no storage
local/S3. A barreira impede edição/download legado. Depois realiza ingestão SQL
idempotente. Crash entre barreira e SQL deixa estado pendente recuperável por
retry, jamais retorno automático ao legado. O baseline permanece na barreira
durante o prazo original. Divergências de linhas, atualização ou saldos entre
job e JSON são conflitos; não se escolhe uma representação silenciosamente.

**Pré-condição de ativação:** drenar todos os binários antigos e não expor a
análise antes de concluir sua adoção. Um binário anterior que desconhece a
barreira não pode participar do piloto. Nem a barreira S3 nem o lock de cache
são apresentados como CAS distribuído. Os métodos de ingestão são internos;
não há endpoint de migração em massa ou rotina que importe documentos ativos.

`CanonicalReviewAdapter` preserva o contrato de edição/recibo existente. É
instanciado por request/escopo, nunca compartilhado entre usuários. Não faz
fallback para storage legado após erro SQL. Downloads com overrides devem
primeiro confirmar configuração por esse comando; um GET não ganha um writer
independente de configurações canônicas.

### Publicação de artefatos

`CanonicalArtifactService` usa os exportadores atuais e um snapshot SQL de
versão definida. Cada tentativa escreve chaves próprias:
`canonical/<analysis>/v<version>/<attempt>/<artifact>`.
Somente depois de escrever todos os arquivos publica o manifesto em SQL,
revalidando escopo, prazo e versão. Uma tentativa que perdeu a corrida não pode
substituir bytes publicados por outra tentativa.

O download busca somente manifesto da versão atual, valida SHA-256 e revalida
versão/autorização/expiração depois de ler o objeto. Falha de geração ou storage
deixa a edição confirmada e permite retry; não devolve o arquivo anterior como
atualizado. Não existe transação atômica entre PostgreSQL e S3.

Adapters de objetos local e S3 usam chaves privadas; S3 mantém criptografia
AES256 por padrão ou as opções KMS fornecidas. O teste de concorrência e falhas
usa PostgreSQL real e filesystem real. Os testes S3 usam cliente simulado;
bucket real e canário permanecem gates de integração da OFX-70.

## Retenção, expurgo e recuperação

Prazo efetivo inicial = mínimo dos prazos existentes da análise, job e lote.
Leituras/escritas/downloads respeitam o menor prazo de origem vigente. O schema
recusa extensão do prazo canônico. Não há prazo de 365 dias, nova política
comercial, renovação por retry ou resgate de documento expirado.

`CanonicalCleanupService` elimina todas as tentativas de exportação e a cópia
legada cercada pela barreira; então elimina dados SQL e limpa o payload financeiro
do job. Se o job for removido antes, a cascata remove dados e enfileira o ID para
limpeza de objetos. Erro de storage mantém o trabalho pendente, com retry seguro.

Um advisory lock de **sessão**, compartilhado por geradores e exclusivo para
expurgo, impede objetos tardios após a limpeza. Não mantém uma transação de
correção financeira aberta durante I/O externo. Conexão encerrada por crash
libera o lock. A geração reautoriza depois de adquirir o lock.

Antes de habilitar uma coorte, a OFX-70 deve conectar o cleanup ao scheduler
existente, configurar timeouts de storage, confirmar lifecycle do prefixo S3
como defesa adicional e validar remoção de caches de todas as instâncias.
Backups devem seguir a política vigente e reaplicar exclusões na restauração;
este PR não muda retenção de backups nem configura infraestrutura produtiva.

## Migração e rollback

```powershell
# Ambiente descartável ou implantação posteriormente autorizada, nunca implicitamente:
Set-Location backend
.\venv\Scripts\python.exe -m alembic upgrade head
```

Executar preferencialmente a partir de `backend` com as variáveis de conexão e
schema próprias do ambiente. A migration cria tabelas vazias, índices e triggers;
não atualiza transações/documentos preexistentes. Triggers em tabelas existentes
exigem locks breves de DDL: medir em homologação, usar janela/lock timeout e não
prometer ausência de bloqueio produtivo com base no banco sintético.

Antes da primeira adoção, `alembic downgrade 20261101_01` remove apenas estruturas
novas, após verificar ausência de análises e limpezas pendentes. Upgrade novamente
é suportado. Depois da adoção, downgrade falha transacionalmente e preserva a
revisão Alembic. O rollback operacional desliga novas adoções e mantém binário
compatível com a autoridade SQL; não retorna a writer JSON/S3 antigo.

Não é necessária migração dos documentos antigos: eles permanecem legados até
expirar. Não há backfill, checkpoint financeiro artificial ou reexecução de parser.
Se futuramente for necessário migrar ativos, isso exige fluxo próprio de
divergência/checkpoints, preservando IDs, correções e prazo original.

Monitorar no piloto: conflitos 409, retries, erro de artefato, pendências de adoção,
atraso/erro de expurgo, p95/CPU/RAM, volume de revisões e lock wait. Usar somente
IDs/códigos/versões nos logs técnicos, nunca payload financeiro.

## Evidências de validação

Resultados finais são registrados em `canonical-movements-ofx69-results.json`
e na seção de validação do PR. Testes cobrem PostgreSQL real, duas instâncias,
crash de processo antes/depois do commit, retry concorrente, histórico, versões,
expiração durante comando/download, isolamento, múltiplas contas, repetições,
exportação e limpeza concorrentes, adoção retomável e rollback seguro.

Os gates OFX-67/68 são executados contra a base integrada, sem atualizar goldens
nem afrouxar thresholds. O benchmark local não substitui o ensaio controlado de
homologação exigido pela OFX-67 (p95/CPU/RAM/OCR/HTTP/499).

`POST /analyze` está desativado no contrato atual (405). A validação HTTP usa
`POST /convert` com CSV, `GET /report`, downloads e caminho negativo; não reativa
o endpoint histórico para satisfazer a instrução V1.

A suíte completa serial executada com bancos PostgreSQL locais descartáveis
retornou **4019 passed, 2 skipped, 2 xfailed, 2 warnings em 609,04 s**. Um skip
era a variável de conexão específica de outro ensaio PostgreSQL, executado
separadamente na validação final; o outro é OCR real sem Tesseract. Os xfails
são gaps preexistentes do catálogo sintético, não convertidos em sucesso.
Os warnings são de depreciação do lifespan do FastAPI.

O PRO retornou **49 arquivos / 253 testes aprovados em 82,82 s**; ESLint,
Ruff, UTF-8/mojibake, navegação e `git diff --check` passaram.

Incidente de infraestrutura preservado: a primeira suíte com quatro workers,
executada simultaneamente aos gates, abortou com `MemoryError`/falha nativa do
PDFium (2746 passed e 6 errors antes do encerramento). O processo da baseline
Free também teve `MemoryError`; a comparação resultante não foi aceita. O caso
PDFium de 72 páginas passou isoladamente (1 passed em 2,21 s), e a suíte completa
foi repetida serialmente sem falhas. Não houve alteração de parser/golden nem
relaxamento de thresholds para obter aprovação. Os gates finais foram repetidos
sem concorrência de suítes pesadas; isso não aprova performance produtiva.
