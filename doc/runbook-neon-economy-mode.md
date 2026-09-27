# Runbook — modo econômico do Neon

## Objetivo

Reduzir temporariamente os despertares e o tempo ativo do compute Neon sem
interromper o fluxo principal de conversão. O modo econômico mantém upload,
processamento inline, revisão e downloads no Render, mas suspende a seleção de
novos usuários para o caminho assíncrono AWS.

O modo não altera schema, não remove jobs existentes e não apaga objetos no S3
ou mensagens nas filas.

## Ativação

1. No Render, definir `NEON_ECONOMY_MODE=true` e reiniciar o serviço.
2. No Neon, configurar o compute com mínimo e máximo de `0.25 CU` e confirmar
   `suspend_timeout_seconds=300`.
3. Confirmar que o dispatcher periódico do outbox permanece desabilitado na
   AWS (`EnableOutboxDispatcher=false`).
4. Direcionar health checks e monitores somente para `GET /health`.
5. Não desabilitar o event source mapping enquanto houver mensagens em voo.
   Sem novos lotes, ele pode permanecer habilitado e ocioso.

Com a flag ativa, o backend:

- ignora temporariamente allowlist e rollout percentual AWS;
- informa o runtime legado sem resolver identidade ou consultar PostgreSQL;
- usa pool PostgreSQL web com mínimo `0`, máximo `1` e fechamento ocioso após
  60 segundos;
- mantém o catálogo público de planos em cache por pelo menos 24 horas;
- preserva login, quotas, histórico, checkout e conversões inline.

## Validação após o deploy

Executar sem credenciais:

```text
GET /health
GET /api/conversion-runtime
GET /plans
```

Resultados esperados:

- `/health`: `200`, `{"status":"ok"}`;
- `/api/conversion-runtime`: `architecture_mode=legacy`,
  `execution_mode=inline_legacy` e `direct_batch_enabled=false`;
- `/plans`: `200` com `Cache-Control: public, max-age=86400`.

Depois, validar uma conversão controlada pelo fluxo atual, revisar o resultado e
baixar OFX ou XLSX. No Neon, confirmar que o compute muda para `Idle` após cinco
minutos sem atividade. Evitar usar consultas SQL periódicas para essa
verificação, pois elas próprias reiniciam a janela de suspensão.

## Rollback

1. No Render, definir `NEON_ECONOMY_MODE=false` e reiniciar o serviço.
2. Restaurar os limites de autoscaling anteriores no Neon, se necessário.
3. Confirmar em `GET /api/conversion-runtime` que a seleção percentual voltou a
   produzir `direct_batch_enabled=true` para as identidades elegíveis.
4. Manter S3, PostgreSQL, SQS e DLQ intactos durante o rollback.

O rollback não exige migration nem downgrade de banco.
