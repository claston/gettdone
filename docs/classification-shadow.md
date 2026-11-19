# Classificação determinística em shadow

O núcleo em `backend/app/application/classification_shadow/` pode ser reutilizado
por outros canais. A exposição inicial usa exclusivamente o PRO autenticado,
com lista explícita de IDs de usuários. Nenhuma sugestão é aplicada às tabelas de
classificações financeiras, aos valores, à conciliação ou aos exports.

## Escopo do piloto

- Lançamentos já extraídos: regras próprias, versionadas, para tarifas, juros,
  IOF e pagamentos tributários explícitos. PIX e transferências sem evidência
  suficiente permanecem indeterminados; regras conflitantes também se abstêm.
- NF-e XML: leitura de todos os itens (até 200), com códigos e precisão originais,
  número do item e referência ao XML. NFS-e e itens de notas em PDF ficam fora
  deste piloto. Não há chamada a OCR, IA ou ao Reforma Aberta.
- Extratos existentes podem ter sido extraídos anteriormente com OCR pelo fluxo
  normal; a classificação reaproveita dados prontos e não executa OCR novamente.
- A consulta fiscal usa a data de emissão como referência explícita. Ela não
  confirma fato gerador, regime, destinação, benefícios ou apuração. Mesmo uma
  combinação compatível continua sujeita à revisão. Ausência de vínculo nunca
  resulta em tributação integral automática. Não há inferência de NCM por nome.

## Fonte oficial e artefato

`receita-worker/upstream.json` fixa o repositório oficial da Receita, commit
`150cccbe414d4a5edea703e4f7a424af5aa222c7` e versão 1.6.1 (MIT). A esteira
`Classification shadow | Official Receita ZIP` instala Java 21 e Maven no runner,
gera o SQLite com Flyway, executa os testes oficiais, instala o JAR normal e
compila nosso adaptador. Não usa Docker nem exige Java local.

O ZIP contém JARs em `lib/`, a base SQLite somente para leitura, a licença e um
manifesto. O build verifica 250 MiB descompactados e executa um teste do ZIP
extraído, incluindo o exemplo oficial REG-011. O manifesto registra o hash da
base e do ZIP. Artefatos e relatórios ficam disponíveis no GitHub Actions.

O adaptador inicializa o Spring original uma vez por ambiente Lambda, com porta
efêmera em loopback para compatibilidade da configuração servlet. Ele chama os
serviços Java diretamente. Nenhuma API pública ou Function URL é criada.
O adaptador exclui apenas o controlador e o listener de consulta remota de
versões do perfil offline oficial e desliga a porta de gestão. A inicialização
não consulta a Receita pela internet; as regras e a base permanecem fixadas.
Medir cold start, duração e memória na primeira implantação AWS; teste no runner não
substitui teste no runtime real do Lambda.

## Infraestrutura e implantação

`infra/classification-shadow.yaml` cria recursos separados: Lambda java21 ZIP,
SQS, DLQ, bucket privado com expiração, IAM restrito e alarme de DLQ. Inicialmente
usa 2 GiB, timeout de 180 segundos e concorrência 2, a ajustar com medições.
A fila nasce desabilitada. Não altera o worker de conversão.

O workflow manual `CD | Deploy Classification Worker (ZIP)` segue o padrão da
conversão: branch `main`, confirmação `deploy=true`, ambiente protegido
`production-worker`, aprovação existente e credenciais temporárias pela role
`gettdone/GettDoneGitHubWorkerDeploy`. Reutiliza as variáveis `AWS_ACCOUNT_ID` e
`AWS_WORKER_DEPLOY_ROLE_ARN`; não exige um ambiente novo nem ID de outro build.
A região é `us-east-1`, como na conversão.

Depois da aprovação, confere a CI de produto do commit exato, compila e testa a
Receita e o adaptador, executa o ZIP, publica no S3 versionado e prepara um
change set. Só aceita mudança no código da Lambda fiscal, sem substituição do
recurso. Preserva todos os outros parâmetros, inclusive `EnableQueue` e
`ProducerRoleName`. Confere o hash do código implantado e registra as coordenadas
do ZIP anterior para rollback. Não altera a esteira nem os recursos da conversão.

A role CloudFormation `gettdone/GettDoneClassificationCloudFormationExecutionRole`
tem leitura do artefato e APIs de atualização restritas a essa Lambda. O
CloudFormation pode chamar tanto `UpdateFunctionCode` quanto
`UpdateFunctionConfiguration`, por isso ambas constam na role; o bloqueio de
mudanças de configuração é feito pela validação integral do change set e pela
preservação dos parâmetros. `iam:PassRole` aceita somente a role de runtime
fiscal para o serviço Lambda. Ver a
[documentação AWS](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-resource-lambda-function.html).
A
preparação da infraestrutura é uma operação administrativa única, como ocorreu
com o worker de conversão. Não é executada pela role de publicação de código.

### Preparação única na AWS

1. Na conta de produção, em `us-east-1`, criar a stack
   `gettdone-classification-bootstrap` com
   `infra/classification-shadow-bootstrap.yaml`, reconhecendo
   `CAPABILITY_NAMED_IAM`. O template cria o bucket privado versionado
   `gettdone-classification-artifacts-<CONTA>-us-east-1` e a role CloudFormation
   de atualização de código. Manter as versões dos artefatos para rollback.
2. Gerar a policy adicional abaixo, revisar e anexar como policy inline
   `GettDoneClassificationZipRelease` à role **existente**
   `gettdone/GettDoneGitHubWorkerDeploy`. Não substituir suas policies atuais
   nem mudar a trust policy OIDC do ambiente `production-worker`.

   ```powershell
   backend\venv\Scripts\python.exe scripts/classification_shadow_deploy_policy.py --account <CONTA>
   ```

3. Após o merge, executar `Classification shadow | Official Receita ZIP` em
   `main`. Baixar o artefato, localizar `receita-shadow.zip` e `evidence.json`,
   conferir SHA-256, `application_commit` e os três resultados `passed`.
   Com credenciais administrativas, enviar **esse ZIP** ao bucket de artefatos
   na chave `classification-shadow/releases/<zip_sha256>.zip`, com SSE-S3.
   Guardar o `VersionId` retornado pelo S3. Esta primeira carga é necessária
   porque o Lambda Java precisa de um pacote válido para ser criado.
4. Criar a stack `gettdone-classification-shadow` com
   `infra/classification-shadow.yaml`, parâmetros `ArtifactBucket`,
   `ArtifactKey`, `ArtifactVersion`, `EnableQueue=false` e o nome da role
   efetivamente usada pelo backend em `ProducerRoleName`, se houver.
   Reconhecer `CAPABILITY_NAMED_IAM`. A Lambda terá nome
   `gettdone-classification-shadow-worker`. Usar credenciais/permissões de
   provisionamento; a role de atualização de código do passo 1 não cria
   infraestrutura. Se o backend usa um usuário IAM, deixar `ProducerRoleName`
   vazio e conceder as ações da `ProducerPolicy` à identidade real em uma
   operação administrativa própria; não inventar uma role de backend.
5. Publicar a aplicação/migração e configurar o backend conforme abaixo.
   Habilitar `EnableQueue=true` em uma atualização administrativa da stack para
   o teste com NF-e sintética. As publicações seguintes preservam esse valor.

O workflow de publicação atualiza uma stack que já existe; não cria roles,
filas, buckets ou a Lambda. Depois da preparação, basta selecioná-lo em Actions,
usar `main`, marcar `deploy=true` e aprovar no ambiente `production-worker`.
Os artefatos `deployment-before.json`, `change-set.json` e `deployment.json`
registram o pacote anterior, o diff revisado e o resultado. Se o processo falhar,
consultar os eventos da stack; as coordenadas de rollback são salvas antes da
alteração. O build em PR não recebe credenciais AWS.

No backend, configurar com os outputs da stack:

```text
CLASSIFICATION_SHADOW_MODE=shadow
CLASSIFICATION_SHADOW_USER_IDS=<IDs internos separados por vírgula>
CLASSIFICATION_SHADOW_BUCKET=<output Bucket>
CLASSIFICATION_SHADOW_QUEUE_URL=<output QueueUrl>
```

O PRO precisa ter a Central Financeira e a gestão fiscal existentes habilitadas
para os usuários do piloto. O painel recebe a permissão do backend; uma flag
visual não concede acesso. Para testar só movimentos, bucket/fila são opcionais.

## Processamento e retenção

O POST cria uma execução identificada por origem, versão, regras e hash. Para
NF-e, persiste o pedido no banco antes de publicar um objeto privado e uma
mensagem SQS contendo apenas o UUID. Repetir o pedido devolve a mesma execução.
Enquanto o painel consulta o status, publicações pendentes podem ser reenviadas
a cada dois minutos, até cinco tentativas. SQS também faz suas tentativas, com
resposta parcial; resultado imutável impede duplicação. Após 15 minutos sem
resultado, a interface indica falha. Solicitar novamente uma execução que falhou
reinicia as tentativas, preservando a entrada e o prazo de expiração originais.
Reabrir uma execução não aplica nada.

Runs e avaliações expiram em até 24 horas, limitados pela validade do documento
original. Toda consulta e avaliação reconfere proprietário, plano, origem e
versão. A limpeza fiscal existente remove runs expirados e avaliações em
cascata. O bucket usa Lifecycle de um dia: exclusão física é assíncrona; a API
nega acesso imediatamente no prazo da origem/run. Logs não recebem conteúdo
financeiro. SQS/DLQ carregam somente o UUID e retêm mensagens por um dia.

## Migração e rollback

A revisão `20261117_01` adiciona duas tabelas isoladas e índice de expiração.
Não há backfill ou alteração de tabelas financeiras existentes. O downgrade
preserva dados shadow e permite voltar à aplicação anterior; o upgrade é
idempotente. Não há FK das novas tabelas para tabelas anteriores. A CI com
PostgreSQL verifica dados existentes, upgrade/downgrade/reupgrade e expiração.

Para desligar, configurar `CLASSIFICATION_SHADOW_MODE=off` e desabilitar o
EventSourceMapping. Manter a limpeza fiscal e o Lifecycle ativos. Reimplantar
um ZIP anterior pelo S3 versionado se necessário, usando `ArtifactKey` e
`ArtifactVersion` salvos em `deployment-before.json`, junto à versão correspondente
do backend (o contrato rejeita versões diferentes). Fazer a atualização por
CloudFormation para manter o estado da stack. Não fazer DROP de dados como
parte de rollback operacional. Depois de uma publicação, a stack usa a role
CloudFormation limitada a código; alterações administrativas em filas, IAM ou
configurações exigem uma role de provisionamento apropriada, explicitamente
selecionada na atualização. O workflow de código não faz essa elevação.

## Validação e acompanhamento

- Testes de regras: contexto, sinal, conflitos, PIX ambíguo, preservação de dados.
- XML: ausência de códigos, itens repetidos, precisão e proveniência; sem OCR.
- HTTP real/PostgreSQL: allowlist, CSRF, dono, idempotência, feedback separado,
  versões alteradas, TTL e rollback aditivo.
- Java: casos oficiais compatíveis e incompatíveis, mistura de itens, ausência
  de NCM, nenhuma sugestão integral por falta de anexos e execução do ZIP real.
- PRO: avaliação explícita, feedback isolado, invalidação ao mudar a origem.
- Suítes existentes continuam cobrindo API, exports e conciliação.

Em AWS, validar uma mensagem sintética via SQS e conferir o resultado no PRO,
verificar duração/memória/cold start, erros Lambda, fila e DLQ. Comparar sugestões
com avaliações humanas; concordar com o XML por si só não mede correção fiscal.
O painel mostra concordâncias, divergências e itens que o avaliador não sabe.
