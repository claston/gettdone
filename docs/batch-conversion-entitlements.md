# Direitos de conversão em lote por plano

O acesso comercial ao lote é configurado em cada versão de plano, sem depender
do nome ou código do produto:

- `batch_conversion_enabled`: habilita a criação e o envio de lotes;
- `batch_max_files`: limita o número de arquivos por lote, entre 1 e 12.

Esses campos pertencem a `plan_versions`. Assim, uma nova versão pode alterar o
direito sem mudar contratos antigos já associados a outra versão.

## Separação entre direito comercial e rollout técnico

O direito do plano não seleciona a arquitetura de execução. Allowlist e rollout
percentual continuam decidindo se uma identidade será encaminhada ao worker. O
limite efetivo é o menor entre:

1. o máximo configurado no plano;
2. o máximo permitido pelo runtime/rollout do worker;
3. o teto operacional da API, atualmente 12.

Uma identidade sem direito a lote ainda pode ser encaminhada ao worker para uma
conversão individual. Ela não pode usar a rota PRO nem contornar a regra enviando
múltiplos arquivos pela rota técnica `/api/conversion-batches`.

## Migração e compatibilidade

A migration `20261012_01` é aditiva. Ela habilita lote com limite 12 para todas
as versões de plano já existentes, que representam os planos pagos atuais. Uma
nova versão de plano nasce com lote desabilitado e limite 1 até que a configuração
seja definida explicitamente.

O código anterior ignora as colunas novas, portanto continua compatível durante
a janela de rollout. Após o deploy da aplicação, a inicialização PostgreSQL passa
a exigir que a migration tenha sido aplicada.

O downgrade remove apenas a constraint e as duas colunas adicionadas. Se for
necessário reverter somente o comportamento, é preferível manter a migration e
definir `batch_conversion_enabled = FALSE`, evitando perda da configuração.

## Verificação pós-deploy

1. Confirme que os planos pagos atuais expõem `batch_conversion_enabled=true` e
   `batch_max_files=12` em `GET /plans`.
2. Confirme que um usuário pago selecionado para o canário cria e envia um lote.
3. Confirme que usuário Free/anônimo recebe `403 batch_plan_required` na rota PRO.
4. Confirme que mais arquivos do que o limite do plano retorna
   `400 batch_file_limit_exceeded` antes de preparar uploads.
5. Confirme que retirar o direito entre criação e envio bloqueia o submit.
