# Histórico de recusas por cota no fluxo assíncrono

Quando a cota já está esgotada, a criação do lote retorna HTTP 429 antes de
preparar o upload ou criar jobs. Essa recusa agora é gravada no histórico
existente: `anonymous_conversion_events` para anônimos e `user_conversions`
para cadastrados. No PostgreSQL, essas tabelas pertencem ao schema configurado
pela aplicação (`gettdone` em produção).

Cada arquivo recusado gera um registro com `status=Falha`,
`error_code=quota_exceeded`, `error_stage=batch_preflight` e um destes subcódigos:

- `weekly_quota_exceeded`: cota semanal de conversões;
- `monthly_pages_quota_exceeded`: cota mensal de páginas;
- `pro_monthly_pages_quota_exceeded`: orçamento mensal separado do OCR PRO.

O identificador do registro deriva da identidade, da chave de idempotência,
do modo OCR e dos metadados/posição do arquivo no pedido. Repetir o mesmo pedido
atualiza o registro existente; uma nova chave representa uma nova tentativa.
O painel administrativo classifica esses registros como recusas de negócio
(`non_conversion_count`), sem inflar o total de conversões ou falhas técnicas.

Não há consumo adicional de cota, upload, chamada de OCR nem publicação na fila.
Páginas e representação do documento ficam desconhecidas; páginas de OCR ficam
em zero. O nome e o SHA-256 são declarados pelo cliente: o arquivo ainda não foi
recebido ou verificado. Ao analisar fraude, usar `error_stage=batch_preflight`
para distinguir esse hash dos hashes calculados sobre arquivos processados.

Se a cota acabar depois do enfileiramento, o worker já registra `quota_exceeded`
pelo pipeline de conversão. Os testes cobrem esse caso e a reentrega do mesmo
job para anônimos, usuários gratuitos e assinantes com cota em páginas.

## Implantação e validação

A correção está na API; não exige migração de banco, alteração do cliente nem
mudança na lógica do worker. O contrato da resposta HTTP 429 permanece igual.
As recusas antigas que não foram persistidas não são recuperadas automaticamente.

Para verificar, esgotar a cota de uma identidade de teste e enviar um pedido a
`POST /api/conversion-batches`. Conferir HTTP 429, um registro por arquivo no
histórico e ausência de novos uploads/jobs. Repetir com a mesma chave e verificar
que a quantidade de registros não aumenta. Enviar uma nova chave e verificar
uma nova tentativa. Conferir também `non_conversion_count` no painel.

Falhas de persistência mantêm a resposta HTTP 429 e geram o aviso
`batch_quota_history_persist_failed`, sem nome do arquivo ou conteúdo da exceção.
Monitorar esse aviso após o deploy: nessa situação a recusa continua válida,
mas o histórico pode permanecer incompleto.

Rollback: reverter o código da API. Os registros novos continuam compatíveis
com a versão anterior e devem ser preservados; a lacuna de registro volta até
que a correção seja reaplicada.
