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
serviços Java diretamente. Nenhuma API pública ou Function URL é criada. Medir
cold start, duração e memória na primeira implantação AWS; teste no runner não
substitui teste no runtime real do Lambda.

## Infraestrutura e implantação

`infra/classification-shadow.yaml` cria recursos separados: Lambda java21 ZIP,
SQS, DLQ, bucket privado com expiração, IAM restrito e alarme de DLQ. Inicialmente
usa 2 GiB, timeout de 180 segundos e concorrência 2, a ajustar com medições.
A fila nasce desabilitada. Não altera o worker de conversão.

O workflow manual `Deploy classification shadow ZIP` aceita o ID de uma execução
bem-sucedida da esteira do ZIP no mesmo commit de `main`, confere o hash do
artefato e implanta somente a stack `gettdone-classification-shadow`. Configurar
as variáveis do ambiente GitHub `classification-shadow`:

- `AWS_CLASSIFICATION_DEPLOY_ROLE_ARN`: role OIDC autorizada para a stack shadow,
  seus recursos e upload no bucket de artefatos;
- `AWS_CLASSIFICATION_ARTIFACT_BUCKET`: bucket existente com versionamento ativo;
- `AWS_REGION`: região da stack e do bucket;
- `AWS_CLASSIFICATION_PRODUCER_ROLE_NAME`: role IAM já usada pelo backend, para
  anexar apenas a política de publicação e leitura do shadow.

Revisar o PR e passar a CI antes da implantação. O workflow não é executado em
PR nem instala recursos automaticamente. O build em PR não recebe credenciais
AWS. Para habilitar o consumidor, informar `enable_queue=true` no dispatch.

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
resultado, a interface indica falha. Reabrir uma execução não aplica nada.

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
um ZIP anterior pelo S3 versionado se necessário, junto à versão correspondente
do backend (o contrato rejeita versões diferentes). Não fazer DROP de dados como
parte de rollback operacional.

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
