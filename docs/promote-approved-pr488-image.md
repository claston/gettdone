# Promoção do artefato já aprovado da PR #488

Esta operação usa a imagem existente no ECR, sem build, retag ou incorporação
das PRs #489–#492. O código da aplicação deste repositório não é alterado pela
opção de promoção; ela seleciona somente o artefato já testado.

- Digest aprovado: `sha256:737cd4fb2e9cc40a92607358378ff408990bd67d4b00064735d0d5827213c11f`.
- Tag informativa: `candidate-debian-5d7bf8946f38`; a seleção usa o digest.
- Revisão de implementação da #488: `5d7bf8946f38b95fdd10f0d70437b6c1e7955a72`.
- Commit temporário de merge usado no build: `adf55e74a24679b9a4aaec2f895fdf7a653f1132`.
- Evidência da candidata: [run 40254942573](https://github.com/claston/gettdone/actions/runs/40254942573), jobs `candidate-tests` e `validate-image` aprovados.
- Canário real AWS já aprovado em 26/10/2026, 07:29 de São Paulo: request ID `fe627a89-d313-4736-a5b3-2c96a1f3dc6d`, HTTP 200, sem `FunctionError`, checks CSV/XLSX/OFX/PDF/PDFium/psycopg aprovados. Não é necessário repetir esse canário para promover o mesmo digest.

## Pré-condições e execução

1. Integrar a alteração da esteira em `main` pelo fluxo de PR e aguardar o CI
   desse commit. Não é necessário integrar as demais PRs de aplicação para
   selecionar a imagem existente da #488.
2. Iniciar `CD | Deploy Worker Image (ECR)` em `main`, com `deploy=true` e
   `image_source=pr488-debian-canary`:

   ```powershell
   gh workflow run deploy-worker-ecr.yml --ref main -f deploy=true -f image_source=pr488-debian-canary
   ```

3. O ambiente GitHub `production-worker` exige aprovação do revisor configurado
   (`claston`). Essa proteção permanece ativa; não há atualização direta da
   Lambda nem alteração de IAM para evitá-la.
4. A execução verifica o CI do commit operacional e a evidência histórica exata
   da candidata. Confirma o digest no repositório esperado e executa o gate ECR
   HIGH/CRITICAL existente. Digest ausente, revisão divergente ou scan bloqueado
   interrompem a execução, sem fallback para construir outra imagem.
5. A imagem anterior é registrada antes da alteração em um artefato
   `worker-image-rollback-<run_id>-<attempt>`, com retenção de 30 dias. Nenhum
   parâmetro secreto de ambiente é exportado.
6. CloudFormation usa o template anterior e `UsePreviousValue` para todos os
   parâmetros, exceto `ConversionImageUri`. O change set precisa modificar
   somente `ConversionWorker.Code.ImageUri`, sem substituição da função.
7. Após a atualização, a esteira confirma o digest na stack e preserva
   `EnableQueueTrigger` e `EnableOutboxDispatcher`.

O caminho `build-main` continua disponível para a operação anterior. Ele não
seleciona esta candidata. Uma futura imagem com novas correções de aplicação
exige build e validação próprios; não se deve confundir o commit da esteira
com o código contido no digest promovido.

## Estado anterior consultado

Consulta de leitura via perfil IaC em 26/10/2026:

- Stack: `gettdone-production-worker`, `UPDATE_COMPLETE`.
- Imagem anterior: `sha256:f4361bf0c812e0d2b85163aacdc539c6191c1f8620f22bed0710ba788932eaea`.
- Fila ativa (`EnableQueueTrigger=true`) e outbox desativado
  (`EnableOutboxDispatcher=false`).
- Template: `PackageType=Image`, arquitetura `x86_64`, sem override
  `ImageConfig` de comando/entrypoint.

Esses dados são evidência prévia; a esteira consulta e registra novamente o
estado imediatamente antes da atualização. O digest dessa captura operacional
é a referência para rollback, caso outro deploy ocorra antes.

## Validação após implantar

Confirmar `UPDATE_COMPLETE` e o digest aprovado no resumo. Acompanhar logs de
inicialização/execução, erros, timeouts, idade da fila e DLQ. Verificar um job
controlado e seus artefatos sem consultar a base de PRD ou o Render. O canário
isolado já realizado não constitui teste de todo o caminho real da fila e do
armazenamento de produção.

## Rollback

Usar `previous_image_uri` do artefato da execução. Criar um novo change set na
mesma stack, com o template anterior e todos os parâmetros em
`UsePreviousValue=true`, exceto `ConversionImageUri`, que recebe esse URI por
digest. Revisar novamente que somente `ConversionWorker.Code.ImageUri` muda,
sem substituir função ou alterar fila/outbox; executar pela operação autorizada
e aguardar `UPDATE_COMPLETE`.

Reexecutar o workflow com `pr488-debian-canary` não é rollback: essa opção sempre
seleciona o digest Debian aprovado. A imagem anterior precisa ser preservada no
ECR. A restauração não desfaz resultados já gravados; não há migração de banco
ou reprocessamento automático.
