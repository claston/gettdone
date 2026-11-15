# Central Financeira após confirmação manual do cliente

## Problema e comportamento corrigido

Um extrato concluído sem cliente reconhecido permanecia no armazenamento legado.
A confirmação manual salvava o cliente e as contas, mas não criava a autoridade
consultada pela Central Financeira. Atualizar a lista não resolvia o problema.

Com a recuperação habilitada, a confirmação integra o extrato após salvar sua
identificação. O `POST /api/pro/receipts/refresh` existente também recupera os
extratos ativos já associados, inclusive confirmações anteriores à implantação.
Não é necessário reenviar nem converter novamente os PDFs. A consulta GET da
Central continua somente leitura. A paginação existente limita cada atualização
a 100 documentos; o cliente deve continuar seguindo `next_cursor`.

Uma origem problemática não interrompe os demais documentos nem as páginas
seguintes. A atualização retorna `200` com `next_cursor` e `recovery_issues`,
uma lista de pares `job_id`/`status`: `unavailable` para origem ausente/expirada,
`review_required` para snapshot inválido ou divergente e `pending` para falha
transitória. O frontend percorre todos os cursores, recarrega a lista e mostra
as contagens por situação, sem esconder os extratos válidos. Repetir a
atualização retoma os pendentes, preservando os marcadores de proteção.

O caminho usa a última versão do armazenamento de análise, que é a autoridade
dos documentos legados. O payload do job pode estar atrasado após uma edição;
ele não é usado para sobrescrever os valores revisados. Datas, valores, sinais,
IDs de transação, exclusões, inclusões, anotações e referências de página são
preservados. A revisão humana só é mantida quando corresponde ao mesmo snapshot;
uma edição posterior deixa os movimentos pendentes de revisão.

## Consistência e retenção

- Confirmação e atualização usam o mesmo coordenador de recuperação.
- A autorização verifica proprietário, usuário ativo, cliente, plano PRO pago,
  job concluído e retenção do job, lote, análise e recibo.
- A coorte precisa permitir ingestão, leitura e UI. A permissão de edição é
  copiada sem ser ampliada. Relatórios financeiros especializados permanecem
  no leitor original; não são convertidos em movimentos bancários.
- Edição/exportação legadas e recuperação compartilham um advisory lock do
  PostgreSQL por análise. Ele é adquirido antes dos locks do job e da organização,
  incluindo a seleção da autoridade e a publicação no S3. O lock não depende do
  cache local e é liberado quando a conexão termina.
- Downloads sem alterações de configuração não republicam o cache no S3,
  inclusive nos ZIPs de lote/cliente; assim, não sobrescrevem um marcador
  gravado por uma recuperação concorrente.
- Antes de bloquear o leitor legado, a representação SQL completa é validada em
  um savepoint revertido. Depois disso, o marcador durável impede novas escritas
  legadas e a transação publica movimentos e projeção atomicamente.
- Uma interrupção após o marcador mantém a identificação salva. A confirmação
  informa `503`, sem expor detalhes financeiros, e orienta atualizar a lista.
  Na atualização, essa falha aparece como `pending` no resultado do documento.
  A nova tentativa recupera o snapshot pendente; não há fallback para escrita
  legada. Na confirmação, origem indisponível retorna `410` e necessidade de
  conferência retorna `409`, sempre informando que a identificação está salva.
- Repetir a confirmação ou atualizar a lista não insere novamente a mesma análise
  nem muda os controles de uma autoridade existente. Uploads distintos já
  revisados não são fundidos: a Central mantém sua sinalização de possível
  sobreposição, preservando repetições legítimas.
- O prazo final é o menor dos prazos originais. Nada renova a retenção.

Não há alteração de esquema, migration DDL, exclusão de tabela/coluna nem backfill
global. A recuperação usa as tabelas canônicas existentes e muda a autoridade de
cada análise elegível durante a atualização explícita.

O baseline importado é identificado como `legacy_snapshot`; ele registra o
estado retido na recuperação, sem inventar um histórico de extração anterior.
Os recibos legados retidos continuam no baseline e em `legacy_edit_operations`.
Uma repetição exata de uma operação ainda atual retorna seu resultado; outra
requisição com o mesmo identificador ou uma revisão posterior gera conflito.
Novas revisões continuam na trilha canônica existente.

## Implantação

1. Publicar API com esta versão, mantendo `CANONICAL_PRO_RECOVERY_ENABLED=false`
   (padrão). Não há alteração de esquema. Publicar também o frontend para
   exibir as pendências; o campo adicional é compatível com clientes anteriores.
2. Drenar todos os processos antigos da API. A proteção de edição/exportação
   fica ativa mesmo com a recuperação desligada; não habilitar recuperação
   enquanto houver processos antigos capazes de publicar análises legadas.
3. Conferir em organização de teste as permissões da coorte já existente,
   armazenamento compartilhado com o worker, plano e retenção. A flag não concede
   direitos nem habilita coortes automaticamente.
4. Habilitar `CANONICAL_PRO_RECOVERY_ENABLED=true` na API. Confirmar um extrato
   pendente e conferir os mesmos movimentos na revisão, Central e exportação.
5. Para extratos já identificados, atualizar a lista de Clientes/Extratos até
   concluir os cursores. Conferir cada mês na Central; o mês inicial continua
   sendo o movimento acessível mais recente.

A recuperação faz leitura do armazenamento, pré-validação SQL e inserção dos
movimentos na requisição. Avaliar a latência com lotes reais antes de ampliar a
coorte; o teste local não estabelece um SLA. Não há reprocessamento ou consumo
de nova conversão. Os locks duram a integração de um documento, não o lote todo;
uma disputa por lock acima de 10 segundos interrompe a tentativa de recuperação.

Monitorar `pro_receipt_recovery_pending`, respostas 503 na confirmação,
`recovery_issues` na atualização, contagem de fontes legadas e duração das
requisições. O log inclui somente job e
classe de erro. Uma pendência deve ser retomada antes da expiração, sem remover
marcadores ou devolver o documento a escritores legados.

## Rollback

Antes da habilitação, o código pode ser revertido sem migração de dados. Após a
primeira recuperação, pausar novas tentativas pela flag e manter os leitores SQL
e as proteções de publicação. Reativar temporariamente a recuperação para concluir
pendências identificadas nos logs/armazenamento, se necessário. Não apagar dados
canônicos, ampliar retenção, limpar marcadores manualmente ou reconstruir análises
legadas a partir de snapshots antigos. Manter a versão capaz de reconhecer os
recibos importados até o fim da retenção dessas análises.

## Validação

Os testes novos usam PostgreSQL local descartável e incluem HTTP real, leitura
S3 simulada a partir de outro cache, edições antes/depois da recuperação, replay,
falhas antes do commit, isolamento, plano, expiração, permissões de coorte,
documento sem atividade, retomada e publicação concorrente por conexões distintas.
O caminho padrão de ingestão nova mantém sua recusa de migração legada implícita.

```powershell
$env:CANONICAL_MOVEMENTS_TEST_DATABASE_URL = '<PostgreSQL local descartável>'
backend/venv/Scripts/python.exe -m pytest backend/tests/test_canonical_receipt_recovery.py -q --basetemp "$env:TEMP/gettdone-pytest-recovery"
backend/venv/Scripts/python.exe scripts/test_backend.py
backend/venv/Scripts/python.exe -m ruff check backend
```

No Windows, executar pytest e o runner fora do sandbox conforme `AGENTS.md`.
O runner completo administra e remove seu próprio diretório temporário. O
basetemp do comando direcionado deve ser removido após a validação.

Os 24 testes de recuperação passaram sem skips em PostgreSQL. O fluxo HTTP
cobriu edição legada, confirmação,
Central, replay, workspace, `/report`, exportações OFX/CSV/XLSX, origem inexistente
e retomada após erro. `/analyze` permanece desativado com 405; não foi reativado.

As regressões da revisão da PR 596 cobrem origem expirada, ausente, divergente
e falha após o marcador, cada uma seguida por documento válido. Um caso com
101 documentos confirma que o cursor continua disponível após a falha e que
o extrato válido da página seguinte é recuperado. A etapa SQL do CI inclui
explicitamente `tests/test_canonical_receipt_recovery.py` com
`CANONICAL_MOVEMENTS_TEST_DATABASE_URL`, evitando que esses testes sejam apenas
ignorados na etapa geral sem PostgreSQL. Os testes de frontend verificam a
agregação das pendências entre páginas e o aviso sem bloquear a lista.

A validação local com os três PDFs indicados pelo usuário mostrou 46, 38 e 38
movimentos de julho, agosto e setembro de 2026 na Central, respectivamente,
preservando todos os registros extraídos. Atualizar novamente manteve 122
movimentos. Os arquivos e dados privados não fazem parte do repositório.

A suíte completa desta revisão executou todos os casos com PostgreSQL. O disco
local ficou sem espaço durante a execução: **4.937 passed, 125 failed,
82 errors, 27 skipped e 2 xfailed em 900,67 s**. Os relatórios preservam essa
falha ambiental; todos os 207 casos afetados passaram na nova execução
em 82,10 s, após remover somente os temporários e o banco descartável desta
tarefa. Os runners removeram seus basetemps. No conjunto das duas execuções,
os 5.144 casos não ignorados passaram; isso não representa uma única execução
integral sem falhas ambientais.

A suíte completa do frontend passou: **392 testes em 66 arquivos**. Ruff,
ESLint, checagem de tipos, Prettier dos arquivos alterados e verificações de
UTF-8/navegação passaram. As três ocorrências de `Ã` nos arquivos PRO são
parte da palavra válida `DEMONSTRAÇÃO`, sem texto corrompido.
