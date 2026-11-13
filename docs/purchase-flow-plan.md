# Compra, e-mails HTML e primeiro acesso

Implementação do fluxo aprovado: o comprador informa um e-mail, mas não precisa ter conta. Uma conta ausente só é criada na liberação confirmada pelo administrador. O cliente define a primeira senha por link. A implantação em produção é uma operação separada deste PR.

## Experiência

1. O checkout registra o pedido sem criar usuário e tenta enviar dois avisos independentes: cliente e backoffice. Ambos usam HTML e texto. Uma falha de envio não perde o pedido nem força o cliente a enviá-lo novamente.
2. O backoffice recebe nome, e-mail, WhatsApp, CPF/CNPJ, observações completas, plano, valor/periodicidade, protocolo, data e aceite de contato do checkout. Campos opcionais vazios aparecem como “Não informado”. Esse aceite não é opt-in de marketing nem aceite de criação de conta.
3. O administrador informa o link de pagamento. O e-mail tem “Pagar agora”, endereço selecionável e “Abrir página para copiar o link”. A página usa a área de transferência com seleção manual como alternativa. JavaScript não é incluído nos e-mails.
4. Antes de liberar, o painel consulta se a conta existe e mostra o e-mail e a ação na confirmação. A equipe deve confirmar o pagamento. Conta suspensa ou divergência entre conta e e-mail impedem a liberação.
5. Uma conta existente mantém senha e provedor de login. Uma conta nova recebe papel de cliente, fica inativa e pendente de primeiro acesso, sem sessão e sem consentimento de marketing. Nenhuma senha utilizável é enviada por e-mail.
6. O cliente escolhe a senha, confirma-a e aceita Termos de Uso/Política de Privacidade. A conclusão prova posse do e-mail, ativa a conta e orienta o login normal. Abrir a página não consome o link e não autentica o cliente.

## Templates e configuração

Fontes de produção: `backend/app/email_templates/purchase/`, empacotadas junto ao backend. Galeria offline: `marketing/templates/purchase-flow/index.html`. Execute `backend/venv/Scripts/python.exe scripts/build_purchase_email_preview.py` depois de editar as fontes.

`ContactService.send_text_email` aceita `html` opcional e preserva os chamadores anteriores de texto. O renderer escapa dados, valida URLs e rejeita campos ausentes. O WhatsApp vem de `WHATSAPP_SUPPORT_NUMBER`, com a validação numérica do public-config; o bloco desaparece se a variável estiver ausente ou inválida. Não há descadastro de marketing nas mensagens transacionais.

| Variável | Padrão | Uso |
| --- | --- | --- |
| `CHECKOUT_GUEST_ENABLED` | `true` | Permite pedido sem conta. Desabilitar mantém o checkout autenticado. |
| `CHECKOUT_ADMIN_PROVISIONING_ENABLED` | `true` | Permite criar conta ausente na liberação confirmada pelo admin. |
| `PURCHASE_FRONTEND_URL` | `https://www.ofxsimples.com.br` | Origem das páginas e login; HTTPS, exceto localhost. Sem query/fragmento. |
| `WHATSAPP_SUPPORT_NUMBER` | Sem padrão | Número internacional somente com dígitos. |

HTML e texto são enviados juntos; não há flag separada de HTML. `flow.json` guarda dados fictícios/rótulos da galeria; assuntos efetivos ficam em `purchase_email_service.py`.

## API e autorização

| Endpoint | Comportamento |
| --- | --- |
| `POST /checkout/intents` | Visitante ou autenticado; valida dados/aceite. Autenticado deve usar o e-mail da própria conta. Repetição sequencial de pedido aberto para mesmo e-mail/plano é rejeitada. |
| `GET /admin/checkout/intents/{id}/release-preview` | Sessão administrativa; informa a conta que será usada/criada. |
| `POST /admin/checkout/intents/{id}/payment-link` | Sessão administrativa; envia antes de marcar pagamento como enviado. Falha mantém estado anterior. |
| `POST /admin/checkout/intents/{id}/release` | Sessão administrativa; `{"create_account": true}` necessário se não existe conta. |
| `POST /admin/checkout/intents/{id}/resend-notification` | Sessão administrativa; `kind=release`, `admin_receipt` ou `customer_receipt`. |
| `POST /auth/initial-access` | Token, senha e aceite; não emite sessão. |
| `POST /checkout/payment-page` | Token restrito ao pedido; devolve referência, plano e link atual. |

Campos têm limites de tamanho. Visitantes têm limite adicional de 10 tentativas por IP/hora em cada processo da API; o contador local não substitui controle distribuído na infraestrutura. Pedidos autenticados não usam esse limite adicional.

## Persistência e recuperação

Migração aditiva `20261112_01`: `users.initial_access_pending` (false para existentes), `users.account_creation_source`, `checkout_intents.terms_accepted_at`, tabelas `purchase_tokens` e `checkout_notifications`. Sem backfill de contas ou alteração de senhas existentes.

A liberação cria/vincula conta, ativa assinatura do pedido escolhido, grava evento administrativo e agenda notificação na mesma transação. Repeti-la não renova assinatura nem reinicia cota. PostgreSQL bloqueia por pedido/e-mail; unicidade de e-mail trata corrida com cadastro normal sem sobrescrever credenciais. SQLite usa `BEGIN IMMEDIATE`.

Contas provisionadas usam senha aleatória descartada, permanecem `is_active=false` e têm bloqueio adicional `initial_access_pending` em autenticação, Google, sessões e tokens legados. O painel geral de ativação não pode contornar o primeiro acesso. Reset de senha/verificação comum também não ativam essas contas. O sincronismo da lista de administradores exclui origem `checkout_admin`, inclusive após primeiro acesso.

A suspensão administrativa também funciona antes do primeiro acesso: mantém a conta inativa e revoga todos os convites de definição de senha. Repetir a liberação não restaura o convite; somente a ação explícita de reenviar o primeiro acesso autoriza um novo link. A ativação manual continua bloqueada enquanto o primeiro acesso estiver pendente. Confirmação, reemissão e liberação bloqueiam pedido antes de usuário; suspensão bloqueia usuário antes de revogar tokens, serializando essas operações entre instâncias.

Tokens aleatórios de 32 bytes são persistidos somente como SHA-256. Primeiro acesso: 24 horas, finalidade `setup`, uso único; reemissão administrativa invalida anteriores. Pagamento: 7 dias, finalidade `payment`, consulta o link vigente; deixa de funcionar após liberação e não autentica a conta. O segredo vai no fragmento e é removido do histórico ao abrir a página. Páginas/respostas usam `no-store` e `no-referrer`; erros não ecoam segredos. As páginas não incluem analytics.

Notificações têm unicidade por pedido/tipo, estado `pending/sending/sent/failed`, tentativas e ID do provedor. Cada tentativa usa chave de idempotência derivada de ID/tentativa; eventos registram somente tipo/resultado. “Enviado” significa aceito pelo provedor, sem garantia de entrega à caixa de entrada. `dry_run` é falha nesse fluxo.

O painel permite reenviar recebimento/acesso. Falha no aviso de liberação mantém conta/plano. Reenvio de primeiro acesso cria outro segredo; não recupera o antigo do banco. Tentativa `sending` abandonada pode ser retomada manualmente após cinco minutos. Enquanto pagamento está em envio, a liberação é bloqueada; reenviar o link resolve uma interrupção. Interrupção após aceitação pelo provedor e antes da gravação pode resultar em outra mensagem no reenvio manual. Não há repetição automática pelo worker.

## Implantação e rollback

1. Configurar as duas flags como `false` durante rollout inicial; validar origem, WhatsApp e provedor.
2. Executar `alembic upgrade head` antes de iniciar nova API. Colunas novas têm padrões compatíveis; antigas permanecem. DDL exige locks breves: aplicar fora de transações longas e observar bloqueios. Não há backfill ou manutenção planejada de dados.
3. Atualizar todas as instâncias da API e frontend antes de ativar flags. Não habilitar provisionamento com instâncias de autenticação antigas.
4. Fazer compra fictícia autorizada com destinatário controlado: todos os campos no backoffice, pagamento/cópia, liberação existente/nova, primeira senha e reenvio. Conferir HTML em Gmail/Outlook/Apple Mail; Chromium local não equivale a teste nessas caixas.
5. Monitorar avisos pendentes/falhos e histórico no painel. Consultas operacionais sem segredos:

```sql
SELECT kind, status, COUNT(*) FROM checkout_notifications GROUP BY kind, status;
SELECT COUNT(*) FROM users WHERE initial_access_pending = true;
SELECT intent_id, kind, updated_at FROM checkout_notifications WHERE status IN ('failed', 'sending');
```

Rollback operacional: desligar flags e manter versão com bloqueios de primeiro acesso e promoção automática de administradores. Manter conclusão/reenvio para compradores provisionados. Não apagar contas, tabelas, tokens ou assinaturas. Downgrade Alembic é bloqueado enquanto houver contas pendentes; quando permitido, conserva objetos aditivos. Não remover a versão de credencial da migração anterior.

## Validação

Testes cobrem pedido sem usuário, backoffice completo, escaping/WhatsApp opcional, autorização, confirmação administrativa, credenciais, suspensão/divergência, atomicidade, concorrência, expiração/uso único/reemissão e falhas do provedor. HTTP real usa provedor local fictício, conversão CSV, download de relatório e caminho negativo. `/analyze` permanece desabilitado (405); a API funcional é `/convert`.

`test_purchase_migration.py` valida upgrade/downgrade/re-upgrade com escritores antigos, bloqueio de rollback inseguro e liberações/conclusões concorrentes em PostgreSQL isolado, configurado por `PURCHASE_TEST_DATABASE_URL` exclusivamente em localhost. A suíte normal também exercita SQLite. Nenhum teste envia mensagens a clientes reais.
