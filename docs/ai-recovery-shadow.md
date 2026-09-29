# Diagnóstico sombra de warnings com Amazon Nova

Este fluxo analisa conversões de PDF que terminaram com transações, layout específico reconhecido e
`balance_consistency_failed`. Ele é separado da conversão normal: qualquer falha de S3, SQS, Lambda ou
Bedrock é registrada, mas não altera o OFX nem a resposta entregue ao usuário.

## Fluxo e dados

1. O pipeline compartilhado pelos modos inline e SQS/Lambda verifica a elegibilidade.
2. O PDF **original**, a saída determinística, os warnings e as evidências de origem são gravados como
   objetos restritos e imutáveis. `ready.json` é sempre publicado por último.
3. Uma mensagem pequena, contendo somente bucket, chave do manifesto e chave de idempotência, entra na
   fila exclusiva.
4. A Lambda valida contrato, expiração, tamanho e SHA-256 antes de chamar o Bedrock.
5. O Nova recebe o PDF original e a saída do conversor. O prompt trata todo o documento como evidência
   não confiável e exige transcrição visual, causa provável do parser e correção/regressão sugerida.
6. Um comparador local cruza as duas sequências e registra códigos objetivos junto da explicação do Nova.
7. O diagnóstico bruto fica no prefixo restrito. Só depois é tentada uma fixture canonical v3. Ela é
   publicada separadamente apenas se a validação de privacidade bloquear nome, CPF/CNPJ, conta, endereço
   e identificadores. Falha nessa etapa retém a fixture, mas preserva o diagnóstico restrito.

O artefato original expira pelo lifecycle do bucket. Nenhum PDF, descrição ou resposta do modelo é
colocado na mensagem SQS, nos logs ou no banco de dados.

## Recursos isolados

O template [`infra/ai-recovery-shadow.yaml`](../infra/ai-recovery-shadow.yaml) cria:

- bucket privado com lifecycle separado para originais, resultados restritos e fixtures;
- fila de diagnóstico e DLQ próprias;
- Lambda própria usando a mesma imagem imutável do worker, mas com o comando
  `app.workers.ai_recovery_lambda.lambda_handler`;
- concorrência reservada igual a 1 e gatilho inicialmente desligado;
- role de consumo e Bedrock;
- managed policy de publicação para os runtimes de conversão.

A managed policy exibida em `ProducerAccessPolicyArn` precisa ser anexada tanto ao runtime da API inline
quanto à role da Lambda de conversão. Isso é deliberadamente manual porque esses produtores pertencem a
stacks diferentes.

## Configuração do produtor

Use as saídas `RecoveryBucketName` e `RecoveryQueueUrl`:

```text
AI_RECOVERY_MODE=shadow
AI_RECOVERY_S3_BUCKET=<RecoveryBucketName>
AI_RECOVERY_SQS_QUEUE_URL=<RecoveryQueueUrl>
AI_RECOVERY_MODEL_ID=us.amazon.nova-2-lite-v1:0
AI_RECOVERY_AWS_REGION=us-east-1
APP_RELEASE=<commit implantado>
```

Mantenha `AI_RECOVERY_MODE=off` em todos os produtores até a role/policy, lifecycle e alarmes terem sido
verificados. O modo `active` não substitui resultados de conversão nesta implementação; ele apenas aplica
o limiar de confiança mais conservador já definido pela elegibilidade.

## Ensaio controlado para 01/10

1. Implantar a imagem e a stack com `EnableQueueTrigger=false`.
2. Anexar `ProducerAccessPolicyArn` aos dois produtores e configurar as variáveis, ainda com modo `off`.
3. Confirmar bucket sem acesso público, lifecycle, DLQ, concorrência 1 e acesso ao inference profile.
4. Ativar o gatilho e definir `AI_RECOVERY_MODE=shadow` somente no produtor escolhido para o primeiro caso.
5. Converter um PDF autorizado com layout reconhecido e `balance_consistency_failed`.
6. Confirmar os quatro objetos de request, uma mensagem consumida e `diagnostic.json` restrito.
7. Conferir se a fixture existe. Se estiver retida, consultar somente o erro técnico nos logs e manter o
   original/diagnóstico no perímetro restrito.
8. Comparar visualmente evidências, códigos do comparador e explicação do Nova antes de entregar a fixture
   ao Codex.
9. No rollback, definir `AI_RECOVERY_MODE=off` nos produtores e desabilitar o event source mapping. A
   conversão normal não precisa ser revertida.

Alarmes mínimos: mensagens visíveis mais antigas, DLQ maior que zero, erros/throttles da Lambda e duração
p95. O primeiro ensaio deve usar um único documento autorizado e conferir o custo/tokens em
`diagnostic.json` antes de ampliar o volume.
