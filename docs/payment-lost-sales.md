# Pagamento e encerramento de vendas

O e-mail de pagamento tem o botão **Pagar agora** e o endereço de pagamento visível. Novos envios não geram links para `pagamento.html`; links de e-mails antigos continuam válidos enquanto o pedido aguarda pagamento e o token não expira.

## Operação no painel

- **Enviar link** confirma nome, e-mail e plano do pedido antes de enviar, inclusive nos reenvios. Cancelar não altera o pedido. Durante o envio, cliques repetidos são bloqueados.
- **Marcar como venda perdida** está disponível em pedidos `REQUESTED`, `AWAITING_PAYMENT` e no estado legado `pending`. Exige confirmação e grava `LOST`, com o evento `SALE_LOST`, administrador, horário e status anterior no histórico existente.
- Pedidos perdidos aparecem no filtro **Venda perdida** e em **Todos**. Não permitem enviar link, reenviar avisos nem liberar o plano. Uma nova compra precisa de um novo pedido. Não há reabertura automática.
- O encerramento não altera a conta, o plano ativo nem a cota do cliente e não envia e-mail de perda. O endereço de pagamento original permanece no histórico administrativo; a API do cliente deixa de oferecê-lo e os tokens internos de pagamento são invalidados.
- Uma cobrança externa já enviada precisa ser cancelada no provedor de pagamento. O sistema recebe URLs inseridas manualmente e não tem integração para cancelar essas cobranças.
- Um envio em andamento impede o encerramento. Se um processo cair deixando uma notificação em `sending`, verifique o provedor antes de usar o reenvio existente para recuperar o estado; não encerre nem altere esse registro diretamente sem verificar a entrega.

Na área do cliente, qualquer pedido aberto oculta os dois botões de upgrade e fecha a seleção de planos. O acompanhamento e o pagamento continuam disponíveis. A atualização automática libera novamente as opções elegíveis quando não houver mais pendências. Falhas na consulta mantêm os upgrades ocultos. Pedidos antigos múltiplos são preservados; o último encerramento é que libera as opções.

A consulta `/checkout/intents/latest` mantém a prioridade dos pedidos abertos. Pedidos liberados e perdidos têm a mesma prioridade e são ordenados pela data de criação, do mais recente para o mais antigo. Assim, um upgrade perdido posterior aparece na tabela da área do cliente mesmo quando há uma compra anterior já liberada e o acesso não informa `checkout_intent` na URL.

## API e concorrência

`POST /admin/checkout/intents/{intent_id}/lose` exige administrador. Retorna `200` com `status: LOST` e `next_step: ORDER_CLOSED`; repetir o encerramento retorna `200` sem duplicar o evento. Pedido inexistente retorna `404`; pedido liberado ou com envio em andamento retorna `409`. Envios e liberações posteriores também são recusados no servidor.

A transição, a invalidação dos tokens e a auditoria são uma única transação, com bloqueio do pedido (`FOR UPDATE` no PostgreSQL; `BEGIN IMMEDIATE` no SQLite). O envio reserva a notificação sob o mesmo bloqueio antes de chamar o provedor. Isso evita encerrar um pedido enquanto o envio está em andamento. Os links externos já entregues não são revogáveis por essa transação.

## Produção e rollback

Não há migração, coluna nova ou backfill: `checkout_intents.status` já é texto e os eventos/tokens usam tabelas existentes. Nenhum pedido antigo é alterado durante o deploy.

Publique primeiro o backend em todas as instâncias e só então disponibilize o painel com a ação de perda. Durante a troca de versões, suspenda as operações administrativas de pagamento até todas as instâncias estarem atualizadas. O backend anterior só bloqueava o envio para pedidos liberados e não conhece a restrição de `LOST`.

Para rollback, preserve os dados e eventos de perda. É seguro reverter apenas a apresentação, mantendo as proteções de `LOST` no backend. Para voltar ao backend anterior, suspenda previamente os endpoints administrativos de envio, reenvio e liberação e mantenha-os suspensos até aplicar as proteções de estado nessa versão. Não converta pedidos perdidos para abertos ou liberados para facilitar o rollback.

Após o deploy, use um pedido de teste para verificar envio confirmado, encerramento, filtro, histórico, resposta `409` a novo envio e retorno dos upgrades. Acompanhe erros `409` por ações concorrentes e falhas de entrega `502`/`503` pelos logs e pelo histórico do pedido.

## Validação local

- `scripts/test-backend.ps1`: suíte completa, com diretório temporário exclusivo e limpeza automática.
- `backend/venv/Scripts/python.exe scripts/smoke_admin_orders.py`: confirmações, cancelamento, falhas recuperáveis e perda em desktop/celular, com dados fictícios.
- `backend/venv/Scripts/python.exe scripts/smoke_client_upgrade.py`: múltiplos pedidos, perda, liberação, falha de consulta e retorno dos upgrades.
- `backend/tests/test_checkout_payment_link_http.py`: HTTP real com servidor e provedor locais; envio concorrente com encerramento, preservação de plano/cota, compra nova, conversão e download de relatório.

O endpoint de upload atual é `POST /convert`; `POST /analyze` retorna `405` por decisão anterior do produto. A validação HTTP verifica explicitamente ambos, além de `GET /report/{processing_id}` e relatório inexistente (`404`).
