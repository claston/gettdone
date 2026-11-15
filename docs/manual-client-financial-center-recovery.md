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
- Uma interrupção após o marcador mantém a identificação salva. A API informa
  `503`, sem expor detalhes financeiros, e orienta atualizar a lista. A nova
  tentativa recupera o snapshot pendente; não há fallback para escrita legada.
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
   (padrão). Não é necessária alteração no frontend nem no esquema.
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

Monitorar `pro_receipt_recovery_pending`, respostas 503 na confirmação/atualização,
contagem de fontes legadas e duração das requisições. O log inclui somente job e
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

Os 19 testes novos passaram. O fluxo HTTP cobriu edição legada, confirmação,
Central, replay, workspace, `/report`, exportações OFX/CSV/XLSX, origem inexistente
e retomada após erro. `/analyze` permanece desativado com 405; não foi reativado.

A validação local com os três PDFs indicados pelo usuário mostrou 46, 38 e 38
movimentos de julho, agosto e setembro de 2026 na Central, respectivamente,
preservando todos os registros extraídos. Atualizar novamente manteve 122
movimentos. Os arquivos e dados privados não fazem parte do repositório.

A suíte completa com PostgreSQL terminou com **5.136 passed, 27 skipped e
2 xfailed em 683,85 s**. Essa execução iniciou antes do último ajuste que torna
downloads S3 sem overrides somente leitura. O estado final dos módulos afetados
foi revalidado separadamente: **41 passed**, incluindo as três regressões novas
de download OFX/CSV/XLSX, recuperação e HTTP. Ruff e a varredura de mojibake
passaram. O runner removeu seu basetemp; o banco PostgreSQL descartável e os
diretórios temporários das validações direcionadas foram encerrados/removidos.
