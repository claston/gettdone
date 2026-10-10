# Captura temporária de requests de diagnóstico

Este fluxo observa conversões de PDF que terminaram com transações, layout específico reconhecido e
`balance_consistency_failed`. Ele é separado da conversão normal: qualquer falha de captura é registrada,
mas não altera o OFX nem a resposta entregue ao usuário.

O Bedrock está desativado. O runtime pode preservar o material restrito por 24 horas para análise futura,
mas não publica mensagens na fila e não possui permissão IAM para invocar modelos.

## Fluxo e dados

Documentos recusados por `unsupported_document_type` também são preservados para revisão manual. Nesses casos,
`ready.json` e `deterministic.json` registram tipo, confiança, versão do classificador, decisão e IDs de evidência;
o request não é enviado à fila nem ao Bedrock, mesmo que a invocação esteja habilitada.

1. O pipeline compartilhado pelos modos inline e SQS/Lambda verifica a elegibilidade.
2. Com `AI_RECOVERY_MODE=shadow`, o PDF **original**, a saída determinística, os warnings e as evidências
   de origem são gravados como objetos restritos e imutáveis. `ready.json` é sempre publicado por último.
3. Com `AI_RECOVERY_BEDROCK_ENABLED=false`, nenhuma mensagem é publicada na SQS e nenhuma análise por
   modelo é iniciada.
4. O manifesto marca `expires_at` no máximo 24 horas após a captura.
5. O lifecycle do bucket usa retenção padrão de um dia. A remoção física pelo S3 é assíncrona e pode
   acontecer algum tempo depois do vencimento lógico.

Nenhum PDF ou descrição é colocado em mensagens SQS, logs ou banco de dados.

## Defesas contra chamadas ao Bedrock

O template [`infra/ai-recovery-shadow.yaml`](../infra/ai-recovery-shadow.yaml) mantém os recursos existentes
para uma atualização segura da stack, mas aplica as seguintes barreiras:

- não provisiona `AWS::Lambda::EventSourceMapping` para a fila de recuperação;
- não concede `sqs:SendMessage` aos produtores;
- não concede `bedrock:InvokeModel` nem `bedrock:InvokeModelWithResponseStream` à Lambda;
- configura a Lambda com `AI_RECOVERY_MODE=off` e `AI_RECOVERY_BEDROCK_ENABLED=false`;
- mantém o bucket privado e o prefixo `ai-recovery/requests/v1/` com lifecycle de um dia.

A Lambda também recusa a criação do processador quando `AI_RECOVERY_BEDROCK_ENABLED` não é exatamente
`true`. Essa trava protege contra invocação manual ou mensagens antigas, além das restrições de IAM.

## Configuração do produtor

Para manter somente a captura temporária:

```text
AI_RECOVERY_MODE=shadow
AI_RECOVERY_BEDROCK_ENABLED=false
AI_RECOVERY_S3_BUCKET=<RecoveryBucketName>
AI_RECOVERY_REQUEST_S3_PREFIX=ai-recovery/requests/v1
AI_RECOVERY_REQUEST_TTL_SECONDS=86400
AI_RECOVERY_AWS_REGION=us-east-1
APP_RELEASE=<commit implantado>
```

`AI_RECOVERY_SQS_QUEUE_URL` não é necessário nesse modo. Use `AI_RECOVERY_MODE=off` se nem mesmo os
requests temporários devam ser gerados.

## Implantação e verificação

1. Atualizar a stack para remover o event source mapping e as permissões IAM de SQS/Bedrock.
2. Implantar a imagem nova nos produtores antes de manter `AI_RECOVERY_MODE=shadow`.
3. Confirmar que `AI_RECOVERY_BEDROCK_ENABLED=false` está aplicado nos produtores e na Lambda.
4. Converter um PDF autorizado e elegível.
5. Confirmar os quatro objetos do request e a ausência de mensagens novas na SQS.
6. Confirmar em CloudTrail e nas métricas do Bedrock que não houve `InvokeModel` nem
   `InvokeModelWithResponseStream`.
7. Depois de pelo menos 24 horas, confirmar o vencimento lógico e acompanhar a remoção assíncrona pelo
   lifecycle do S3.

Para rollback da captura, definir `AI_RECOVERY_MODE=off`. Reativar o Bedrock exige uma mudança de código e
infraestrutura revisada, restauração explícita da permissão IAM e nova validação de custos; alterar somente
uma variável de ambiente não é suficiente.
