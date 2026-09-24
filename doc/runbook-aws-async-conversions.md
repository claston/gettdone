# Runbook — conversões assíncronas AWS

## Escopo e princípio de custo

Este runbook ativa o processamento sob demanda sem ambiente permanente de staging. Os recursos são criados em produção, permanecem sem tráfego público até a validação sintética e não usam provisioned concurrency. O código e a imagem da aplicação ficam no repositório público; conta, ARNs, state, parâmetros de infraestrutura e workflow de deploy ficam no repositório privado `gettdone-infra`.

## Perfis operacionais

As variáveis de cada perfil devem ser alteradas juntas e exigem restart do serviço Render.

| Perfil | `ARCHITECTURE_MODE` | `UPLOAD_MODE` | `EXECUTION_MODE` | Documentos | Resultados | Jobs |
|---|---|---|---|---|---|---|
| atual/emergência | `legacy` | `proxy` | `inline_legacy` | `filesystem` | `filesystem` | filesystem |
| fallback compartilhado | `async_aws` | `proxy` | `inline_shared` | `s3` | `s3` | PostgreSQL |
| AWS normal | `async_aws` | `direct_s3` | `sqs_lambda` | `s3` | `s3` | PostgreSQL |

Os nomes completos são `CONVERSION_ARCHITECTURE_MODE`, `CONVERSION_UPLOAD_MODE`, `CONVERSION_EXECUTION_MODE`, `CONVERSION_DOCUMENT_STORE`, `ANALYSIS_STORAGE` e `CONVERSION_BATCH_REPOSITORY`. Combinações diferentes das três acima são rejeitadas pelo backend.

Nos perfis `fallback compartilhado` e `AWS normal`, preserve também o caminho produtivo de documentos escaneados: `TEXTRACT_ENABLED=true`, `TEXTRACT_MODE=text`, `TEXTRACT_FORCE=false`, `TEXTRACT_TEMP_BUCKET` apontando para o bucket compartilhado, `TEXTRACT_S3_PREFIX=textract/tmp` e `PDF_OCR_ENABLED=false`. Essas variáveis não devem ser aplicadas isoladamente antes da atualização das permissões da fundação.

O primeiro rollback deve usar `fallback compartilhado`: novos PDFs voltam a passar pelo Render e são processados inline, enquanto jobs e resultados existentes continuam no S3/PostgreSQL. Se S3 ou PostgreSQL também estiverem indisponíveis, aplicar o perfil `atual/emergência`. O perfil atual não apaga a fila nem jobs assíncronos; eles podem ser retomados depois.

## Canário por usuário com o perfil global legado

Antes do corte global, `CONVERSION_ASYNC_USER_EMAIL_ALLOWLIST` permite enviar somente usuários autenticados e verificados para o fluxo AWS. O backend resolve a identidade pelo token ou cookie de sessão e consulta o e-mail no banco; o frontend não decide sozinho quem participa. E-mails não listados e sessões anônimas permanecem no fluxo legado.

No Render, mantenha as variáveis globais abaixo:

```text
CONVERSION_ARCHITECTURE_MODE=legacy
CONVERSION_UPLOAD_MODE=proxy
CONVERSION_EXECUTION_MODE=inline_legacy
CONVERSION_DOCUMENT_STORE=filesystem
ANALYSIS_STORAGE=filesystem
```

Configure também o caminho assíncrono lateral:

```text
CONVERSION_ASYNC_USER_EMAIL_ALLOWLIST=<email-verificado-do-canário>
CONVERSION_BATCH_REPOSITORY=postgres
DATABASE_URL=<conexão-postgresql-neon-com-tls>
DATABASE_SCHEMA=gettdone
CONVERSION_S3_BUCKET=<bucket-privado>
CONVERSION_S3_PREFIX=conversion/jobs
CONVERSION_RESULTS_S3_PREFIX=conversion/results
CONVERSION_SQS_QUEUE_URL=<url-https-da-fila>
AWS_REGION=us-east-1
```

As credenciais AWS da identidade restrita da API Render devem estar disponíveis pelo provider chain do SDK. Ela precisa apenas das permissões de upload/consulta nos prefixos previstos e `sqs:SendMessage`; não use credenciais da role de infraestrutura. A Lambda continua recebendo banco, schema, Textract e bucket por sua própria configuração.

Sequência do primeiro teste:

1. Publicar backend e frontend com a allowlist vazia; confirmar que o fluxo legado continua funcionando.
2. Configurar as variáveis laterais e adicionar somente o e-mail verificado do canário.
3. Confirmar, autenticado como o canário, que `GET /api/conversion-runtime` retorna `direct_batch_enabled=true`.
4. Confirmar com outro usuário que o mesmo endpoint retorna `direct_batch_enabled=false`.
5. Habilitar o event source mapping da fila e o dispatcher do outbox.
6. Pelo frontend, enviar um PDF controlado, aguardar o lote, revisar uma linha e baixar OFX e XLSX.
7. Conferir status no PostgreSQL, objetos em `conversion/results/`, logs seguros, fila/DLQ e alarmes.

Rollback imediato: esvaziar `CONVERSION_ASYNC_USER_EMAIL_ALLOWLIST` e reiniciar o Render. Isso impede novos lotes AWS sem mudar o perfil dos demais usuários. Jobs já enfileirados podem terminar; se também for necessário interrompê-los, desabilite o event source mapping depois de retirar a allowlist. Não apague S3, filas ou tabelas durante a investigação.

## Rollout percentual com o perfil global legado

O canário da allowlist continua sempre elegível. Para ampliar gradualmente aos
demais identidades (usuários autenticados e sessões anônimas), configure:

```text
CONVERSION_ASYNC_PERCENTAGE_ROLLOUT_ENABLED=true
CONVERSION_ASYNC_ROLLOUT_PERCENTAGE=30
```

A seleção é determinística por identidade: o mesmo usuário ou sessão anônima permanece no mesmo
fluxo enquanto a configuração não mudar. Para rollback imediato dos 30%, altere
somente `CONVERSION_ASYNC_PERCENTAGE_ROLLOUT_ENABLED=false` e reinicie o Render;
a allowlist permanece ativa. O percentual deve ficar entre 0 e 100. Usuários
selecionados pelo percentual podem enviar somente um arquivo por conversão; o
usuário da allowlist mantém o limite configurado de até 12 arquivos por lote.

O event source mapping da SQS deve estar habilitado para consumir os jobs. O
dispatcher periódico do outbox via EventBridge deve permanecer desabilitado
(`EnableOutboxDispatcher=false`): a submissão do lote já publica o evento
pendente de forma síncrona. Não habilite a regra de um minuto neste rollout.

## Infraestrutura mínima no repositório privado

- Um bucket S3 privado, Block Public Access ativo, SSE-S3 e sem versionamento inicialmente.
- CORS limitado às origens HTTPS reais, métodos `POST` e `HEAD`, e headers usados pelo presigned POST.
- Lifecycle: entrada `conversion/jobs/` e staging `textract/tmp/` por 1 dia, resultados `conversion/results/` alinhados a `ANALYSIS_TTL_SECONDS`, e multipart uploads incompletos abortados após 1 dia.
- SQS Standard e DLQ na mesma região da Lambda.
- Redrive com `maxReceiveCount=5`.
- Lambda em imagem Python 3.12 construída por `Dockerfile.lambda`, sem provisioned concurrency.
- Event source mapping com `BatchSize=1`, `MaximumBatchingWindowInSeconds=0` e `ReportBatchItemFailures`.
- EventBridge a cada minuto chamando a mesma Lambda com `{"action":"dispatch_outbox"}`.
- CloudWatch Logs com retenção inicial de 7 dias.
- AWS Budget mensal baixo e alertas em 50%, 80% e 100% do teto definido pelo responsável.

Começar a Lambda com reserved concurrency `2`. Isso permite progresso paralelo em lotes de 12 sem abrir conexões demais no PostgreSQL. Aumentar para `4` somente depois de medir banco, memória, timeout e custo. Cada instância usa pool máximo `1`.

O timeout deve ser definido pelo benchmark do maior PDF suportado. A visibility timeout da fila deve ser no mínimo seis vezes o timeout da Lambda, mais qualquer batching window. O lease do job deve ser maior que o p99 observado e nunca menor que o timeout configurado.

O primeiro corte usa o Textract assíncrono em modo `text`, com polling dentro da invocação limitado a 210 segundos e timeout da Lambda de 300 segundos. A chave temporária e o `ClientRequestToken` são determinísticos por conteúdo/configuração para que retries SQS não iniciem uma segunda análise. Migrar o polling para conclusão SNS/SQS só se os benchmarks mostrarem espera longa ou custo relevante.

## IAM e credenciais

- Role da Lambda: somente leitura/remoção no prefixo de entrada, leitura/escrita no prefixo de resultados e no staging `textract/tmp/`, `Start/GetDocumentTextDetection`, consumo da fila e logs.
- Identidade da API Render: somente criação de presigned POST/`HeadObject`, escrita/leitura nos prefixos necessários, `Start/GetDocumentTextDetection` para o fallback inline e `sqs:SendMessage`. Guardar as credenciais somente como secrets do Render e rotacioná-las.
- Role de deploy local: criada por bootstrap e usada com credenciais temporárias/MFA.
- Role de deploy do GitHub: OIDC com `id-token: write` e `contents: read`; nenhuma `AWS_ACCESS_KEY_ID` ou `AWS_SECRET_ACCESS_KEY` no GitHub.
- Trust policy restrita ao subject atual `repo:claston/gettdone:ref:refs/heads/main`. Pull requests, forks e outras branches não recebem permissão de deploy.
- Actions externas são fixadas por SHA; o workflow exige disparo manual e confirmação explícita.

## Ordem de implantação sem staging

1. Criar o repositório privado e executar o bootstrap IAM localmente.
2. Criar S3, SQS/DLQ, ECR, Lambda, EventBridge, roles, logs, métricas, alarmes e budget com o trigger SQS desabilitado.
3. Construir o primeiro `Dockerfile.lambda` e criar a stack em modo escuro; nas atualizações seguintes, usar o workflow manual OIDC para publicar e trocar somente o digest da imagem.
4. Executar a migração aditiva `20260829_01` antes do backend novo. O backend antigo ignora as novas tabelas.
5. Publicar backend e frontend mantendo o perfil `atual/emergência`.
6. Invocar diretamente a Lambda com jobs sintéticos internos: 1 arquivo, lote de 5 e lote de 12.
7. Testar duplicidade da mensagem, erro permanente, erro transitório, quinta tentativa/DLQ, replay do outbox, expiração e isolamento entre proprietários.
8. Executar carga controlada com concorrência `2`; registrar p50/p95/p99, pico de memória, conexões e custo estimado.
9. Habilitar o event source mapping.
10. Aplicar em uma só mudança o perfil `AWS normal` e reiniciar o Render.
11. Observar os sinais abaixo por pelo menos uma janela completa do maior job.

## Sinais e alarmes

Alarmes acionáveis:

- DLQ com uma ou mais mensagens;
- `ApproximateAgeOfOldestMessage` acima de duas vezes o p99 esperado;
- erros ou throttles da Lambda acima de zero em duas janelas consecutivas;
- duração p95 acima de 80% do timeout;
- outbox pendente mais antigo acima de 2 minutos;
- falhas permanentes acima de 10% dos jobs em 15 minutos;
- budget nos limiares definidos.

Logs estruturados aceitam apenas `job_id`, `batch_id`, `trace_id`, tentativa, resultado, duração, código/classe segura de erro e contagens. Não registrar nome do arquivo, identidade/e-mail, conteúdo, transações, token, cookie, presigned URL, chave S3 completa ou exceção bruta potencialmente sensível.

## Rollback

1. Desabilitar o event source mapping para interromper novas execuções sem perder mensagens.
2. Aplicar o perfil `fallback compartilhado` e reiniciar o Render.
3. Confirmar `GET /api/conversion-runtime`: `direct_batch_enabled=false`, upload `proxy` e execução `inline_shared`.
4. Validar uma conversão pelo endpoint atual e um download de relatório.
5. Manter S3, PostgreSQL, SQS e DLQ intactos durante a investigação.
6. Se S3/PostgreSQL estiverem afetados, aplicar todas as variáveis do perfil `atual/emergência` e reiniciar.
7. Nunca executar downgrade da migração durante rollback de aplicação. As tabelas são aditivas e compatíveis com o código anterior.

## Evidências obrigatórias antes do corte

- build da imagem Lambda e import do handler/SDK Textract dentro do contêiner;
- invocação real do Textract em modo escuro com PDF sintético escaneado, sem fallback local;
- `alembic upgrade head` em banco de teste descartável e SQL de downgrade revisado, sem executar downgrade em produção;
- testes automatizados e lint verdes;
- chamadas HTTP reais: conversão feliz, download feliz, formato inválido e consulta de lote por outro proprietário;
- lote de 12 com conclusão independente por arquivo;
- uma mensagem duplicada sem duplicar cota nem resultado;
- screenshot/registro dos alarmes, lifecycle, Block Public Access e trust OIDC;
- comandos exatos de rollback conferidos por uma segunda pessoa ou em dry run.
