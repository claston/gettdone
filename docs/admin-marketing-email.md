# Disparo manual de marketing (OFX-7)

## Campanha manual: guia do extrato

O admin oferece um envio único da edição `statement-guide-v1`, separado dos
testes individuais. O assunto é **Antes de converter: 4 detalhes para conferir
no seu extrato**, sem `[TESTE]`. O conteúdo é o guia educativo, sem CTA comercial,
com identificação e descadastro individual. Nenhuma ação agenda um disparo.

Depois do deploy e da migração, para enviar manualmente:

1. Abra **Marketing → Enviar o guia para a lista** e atualize o resumo.
2. Confira o assunto e a quantidade de contatos elegíveis. O envio considera
   a lista inteira, independentemente da página e do filtro de busca do admin.
3. Clique em **Preparar lista de destinatários**. Isso registra a lista e o
   conteúdo, mas ainda não envia mensagens. Novos inscritos após esse momento
   não entram nesta edição; prefira preparar perto do horário do envio.
4. Confira a quantidade e clique em **Confirmar envio para N contato(s)
   pendente(s)** quando quiser começar. Para enviar às 07:00 de Brasília,
   faça essa confirmação manualmente nesse horário; não há agendamento.
5. Mantenha a página aberta. Acompanhe aceitos pelo provedor, pendentes,
   excluídos por alteração no cadastro e mensagens para conferência.
   Aceitação não comprova chegada à caixa de entrada.

Use **Pausar após o envio atual** para interromper. Ao fechar a página, o envio
atual pode terminar; os próximos dependem da página aberta. Para continuar,
volte ao mesmo painel, atualize o resumo e confirme os pendentes. Após uma
interrupção abrupta, pode ser necessário aguardar até dois minutos pela
liberação do envio. A campanha concluída não oferece reenvio da mesma edição.

A preparação seleciona contas ativas, verificadas, com opt-in e consentimento
versão 2. Cada contato é conferido novamente imediatamente antes da tentativa.
Descadastro, desativação, troca de e-mail ou renovação do consentimento desde a
preparação impedem o envio pendente; não se concede consentimento automaticamente.
O destinatário definido em `MARKETING_TEST_RECIPIENT` limita somente o teste,
nunca a campanha. Revise sua própria conta se já tiver se descadastrado.

Esta versão usa uma identificação fixa para a primeira edição do guia. Não
apague os registros nem altere essa identificação para repetir o disparo.
Outra campanha ou inclusão de novos inscritos precisa de uma nova edição,
com revisão do conteúdo e confirmação explícita do público.

### Publicação e migração

Não há variáveis novas. Reutilize o Resend, os dados privados do remetente e
`MARKETING_PUBLIC_BASE_URL`. Campanhas exigem `CONTACT_RESEND_DRY_RUN=false`
e uma chave do provedor configurada; simulações não avançam o registro de envios.

Antes de disponibilizar o novo frontend, aplique no banco do backend a migração
`20261115_01` e publique o backend. No ambiente do backend, com o diretório atual
em `/app/backend` na imagem Docker, execute:

```sh
python scripts/run_alembic_migrations.py
```

Se esse comando já estiver configurado no pre-deploy, ele aplicará a revisão nova
no próximo deploy. Se não estiver, execute no Shell do serviço usando as mesmas
variáveis `DATABASE_URL` e `DATABASE_SCHEMA` da aplicação. Não copie segredos para
o repositório. Publique depois o frontend e atualize a página do admin.

A migração adiciona `marketing_campaigns` e `marketing_campaign_deliveries`,
com um índice de estado. Não modifica tabelas ou registros existentes, nem exige
backfill. Os índices são criados somente nas novas tabelas, sem reescrever `users`.
O aplicativo anterior continua funcionando com o esquema expandido. Sem a
migração, somente os endpoints novos respondem `503`; os testes individuais e
demais funcionalidades permanecem disponíveis. SQLite local usa o bootstrap.

### Continuidade e falhas

O banco registra público, conteúdo, remetente, estado, primeira tentativa de
aceitação ainda não esclarecida (ou aceita) e ID
aceito pelo provedor. Os registros incluem e-mails, corpo personalizado e link
de descadastro; ficam restritos ao armazenamento do backend. Não registrar esses
dados ou URLs em logs. O resumo autenticado lista até 200 contatos que precisam
de conferência, para consulta no histórico do provedor.

Um bloqueio de dois minutos no banco serializa o envio entre abas/processos.
Cada requisição envia no máximo uma mensagem, com chave estável por campanha e
destinatário; o navegador aguarda 1,1 segundo entre requisições. Falha de rede ou
do provedor pausa o fluxo, preservando o corpo e a chave para retomada.

O [Resend mantém as chaves de idempotência por 24 horas](https://resend.com/docs/dashboard/emails/idempotency-keys).
Por cautela, uma tentativa de resultado incerto com 23 horas ou mais fica em
**Para conferência**, sem reenvio automático. Uma mudança de elegibilidade após
uma tentativa incerta também exige conferência, pois o provedor pode já ter
aceitado a mensagem. Consulte o histórico do provedor; não apague o registro
para forçar nova tentativa. Limites de envio/quota do provedor podem interromper
a campanha; a interface preserva os pendentes e mostra a falha.

Recusas explícitas reconhecidas do provedor, como `429 daily_quota_exceeded`,
não iniciam essa janela de incerteza quando não existia tentativa incerta
anterior. O destinatário continua pendente e pode ser retomado após a renovação
da cota, inclusive no dia seguinte, com o mesmo corpo e chave. A retomada confere
novamente o consentimento. Se a nova tentativa perder a resposta, registra-se
uma nova janela antes de chamar o provedor. Uma recusa posterior nunca apaga a
evidência de uma tentativa anterior incerta; a limpeza também exige que o
processo ainda detenha o bloqueio da campanha. Timeouts, respostas desconhecidas,
conflitos de idempotência e erros `5xx` continuam conservadoramente incertos.
Referência: [erros e limites do Resend](https://resend.com/docs/api-reference/errors#daily_quota_exceeded).

Rollback: interrompa o envio no admin e restaure frontend/backend anteriores.
Preserve as tabelas e as rotas de descadastro. O downgrade desta migração mantém
o registro de envios intencionalmente, para impedir duplicações em um redeploy;
a reaplicação é idempotente. Não restaure consentimentos revogados nem uma cópia
antiga do registro de envios. Migração, escrita de contas, envio, downgrade e
reaplicação foram validados em PostgreSQL local isolado.

Após o deploy, confira o resumo, os logs HTTP de erros `409/502/503` e os IDs no
provedor. `409` indica lista alterada/vazia ou envio em andamento; `502` exige
retomada com a mesma campanha; `503` exige conferir migração/configuração.

API administrativa (mesma autenticação do teste, respostas `no-store`):

- `GET /admin/marketing/statement-guide`: prévia, contagens e progresso.
- `POST /admin/marketing/statement-guide/prepare`, com `{"expected_eligible": N}`:
  registra a lista se a contagem ainda corresponder; chamadas repetidas retornam
  a mesma campanha sem incluir novos inscritos ou repetir envios.
- `POST /admin/marketing/statement-guide/send-next`, com `{"confirmed": true}`:
  processa o próximo contato pendente; exige a campanha já preparada.

## Teste individual

No admin, abra **Marketing → Disparo de teste → Enviar e-mail de teste**.
O backend aguarda o Resend na própria requisição e apresenta a aceitação pelo
provedor ou a falha. Não usa worker, fila, agendamento ou tarefa em background.

O destinatário é definido exclusivamente por `MARKETING_TEST_RECIPIENT` no
ambiente do backend, sem campo no painel para informar outro endereço.
A conta deve existir, estar ativa, ter e-mail verificado
e `product_updates_opt_in` habilitado. O sistema não cria usuários nem concede
consentimento automaticamente. O seletor oferece dois modelos:

- **E-mail 01 — Guia do extrato** (`01-organizacao`): HTML e texto educativos,
  sem botão de planos, link para converter ou chamada de curso, assunto prefixado por `[TESTE]`
  e link individual de descadastro. Exige consentimento versão 2.
- **Teste básico — Entrega e descadastro** (`marketing-test-v1`): mantém a
  mensagem simples anterior.

O rodapé do e-mail 01 usa a empresa e o CNPJ configurados privadamente no
ambiente do backend. Nome do destinatário e link de descadastro são preenchidos
individualmente. Os dados reais do remetente não devem ser versionados.

O admin seleciona o e-mail 01 inicialmente. A API mantém o teste básico como
padrão quando `template_id` é omitido, preservando os clientes anteriores.
Nenhum desses testes agenda ou dispara mensagens para a lista completa.

O Gmail classifica mensagens automaticamente. Um guia sem chamada comercial
não garante a aba Principal; trocar o CTA de planos por um link de conversão
também não oferece essa garantia. Confira a classificação no teste real,
mantendo identificação e descadastro. Referência:
[categorias do Gmail](https://support.google.com/mail/answer/3094499?hl=pt-BR).

## Configuração

Reutiliza o serviço transacional do Resend, com remetente de marketing opcional,
independentemente do provedor SMTP escolhido para o formulário de contato:

- `RESEND_API_KEY`: chave do Resend já usada pela aplicação.
- `CONTACT_FROM_EMAIL`: remetente dos e-mails transacionais e padrão do marketing,
  autorizado pelo domínio configurado no Resend.
- `MARKETING_FROM_EMAIL`: remetente exclusivo dos envios de marketing. Aceita
  endereço simples ou nome e endereço, como `OFX Simples <novidades@ofxsimples.com.br>`.
  Deve pertencer a um domínio autorizado no Resend. Se ausente, vazio ou composto
  apenas por espaços, mantém o remetente definido por `CONTACT_FROM_EMAIL`.
  Espaços externos são removidos. Configure no ambiente do backend e
  reinicie/reimplante para aplicar. Não altera os remetentes da verificação de
  e-mail, checkout ou formulário de contato. A chave do Resend e a configuração
  de simulação continuam compartilhadas; esta variável separa apenas o remetente.
- `MARKETING_TEST_RECIPIENT`: endereço único da conta de teste, configurado de
  forma privada no serviço do backend (no Render, em **Environment**). Não tem
  valor padrão: ausente, vazio ou inválido bloqueia o envio com `503`. Espaços
  externos são removidos e o endereço é convertido para minúsculas, como no cadastro.
  Configure antes do deploy desta versão e reinicie/reimplante o backend ao alterar.
  Não versionar endereços reais; os testes usam apenas destinatários fictícios.
- `CONTACT_RESEND_DRY_RUN=false`: habilita envio real. O padrão `true` apenas
  simula, e o admin exibe explicitamente que nenhum e-mail foi enviado.
- `MARKETING_SENDER_COMPANY` e `MARKETING_SENDER_CNPJ`: nome da empresa e CNPJ
  usados no rodapé do e-mail 01. Configure os valores reais no Render, no
  serviço do backend, em **Environment**, e aplique com redeploy. Não preencher
  os valores em arquivos versionados, exemplos, logs ou descrições de PR.
  Campos ausentes, vazios ou com marcadores bloqueiam o e-mail 01 com `503`;
  o teste básico continua disponível. O HTML escapa os valores configurados.
- `MARKETING_PUBLIC_BASE_URL`: origem pública que atende as rotas do backend;
  padrão `https://www.ofxsimples.com.br`. Deve ser acessível pelo destinatário e
  encaminhar `/marketing/unsubscribe` ao backend. Em desenvolvimento pode ser
  `http://127.0.0.1:8000`. Não contém caminho, query ou fragmento.
  Origens públicas exigem HTTPS; HTTP é permitido somente no localhost.

Não colocar chaves em arquivos versionados. Não é necessário alterar o worker.

## API e descadastro

- `POST /admin/marketing/test-email`, com sessão admin e JSON
  `{"request_id":"<UUID>","template_id":"01-organizacao"}`. `template_id` é
  opcional e aceita somente `01-organizacao` ou `marketing-test-v1`.
  Não aceita destinatário nem conteúdo arbitrário.
  Retorna `recipient`, `template_id`, `delivery_mode` e `provider_message_id`.
  Aceitação pelo Resend não comprova entrega na caixa de entrada.
- `GET /marketing/unsubscribe?token=...` apresenta confirmação, sem alterar o
  consentimento: leitores e scanners de e-mail podem abrir links automaticamente.
- `POST /marketing/unsubscribe?token=...` cancela marketing sem login. Suporta
  também o POST dos clientes de e-mail via `List-Unsubscribe-Post`.

O token tem assinatura HMAC com finalidade exclusiva, vinculada ao usuário,
e-mail e data do consentimento. Não autentica na conta, não expira por tempo e
deixa de funcionar após novo consentimento ou troca de e-mail/chave de assinatura.
O POST é idempotente e preserva a data/versão originais do consentimento. A opção
`product_updates_opt_in=false` persiste no banco e bloqueia novos disparos.
Mensagens transacionais da conta não são afetadas.

Respostas: `401/403` sem acesso admin, `409` destinatário inelegível, `503`
configuração ausente/inválida, `502` falha ou resultado não confirmado no Resend.
As mensagens não expõem respostas internas do provedor.

O botão fica desabilitado enquanto aguarda. Após falha, preserva o mesmo UUID
para consultar/repetir o disparo sem duplicá-lo dentro da janela do provedor.
Após sucesso, outro clique cria um novo envio. Recarregar a página também cria
um novo UUID; esta versão não mantém histórico persistente de campanhas.
O seletor fica bloqueado durante o envio. Trocar de modelo gera um novo UUID;
as chaves de idempotência também distinguem o teste básico do e-mail 01.
O Resend mantém as [chaves de idempotência por 24 horas](https://resend.com/docs/dashboard/emails/idempotency-keys).
Os [cabeçalhos de descadastro](https://resend.com/docs/dashboard/emails/add-unsubscribe-to-transactional-emails)
seguem a API do provedor. Não registrar URLs completas de descadastro em logs.

## Evolução e compatibilidade

O serviço de marketing e o modelo ficam separados do gatilho HTTP para permitir
futuras sequências. O envio manual para a lista está descrito acima; não existe
agendamento ou disparo automático em background.
Campanhas futuras precisam verificar a elegibilidade em cada envio e respeitar
a versão de consentimento correspondente ao conteúdo (ofertas/parceiros exigem
a abrangência da versão 2).

Para testar antes da campanha: configure os parâmetros privados no Render, publique o backend e o
frontend, selecione **E-mail 01** e clique em **Enviar e-mail de teste**. Confira
o conteúdo educativo e a ausência de chamadas comerciais. No e-mail recebido,
abra **Cancelar recebimento de e-mails** e confirme
o cancelamento. Atualize a lista de contatos no admin: a conta deve deixar de
aparecer e novas tentativas de teste devem ser recusadas. O descadastro é real
e não é desfeito automaticamente ao terminar o teste.

O backend lê os mesmos arquivos `01-organizacao.html`, `01-organizacao.txt` e
`campaign.json` de `marketing/templates/ofx-content-series`. O Docker inclui
explicitamente esses três arquivos. Publique o backend antes do frontend;
versões antigas recusam o novo campo `template_id` sem efetuar o envio.

O teste individual não precisa de tabela nova. Sua única escrita persistente é
a revogação do consentimento, usando campos existentes e SQL compatível com
SQLite/PostgreSQL. O registro da campanha manual usa a migração descrita acima.
O serviço de e-mails mantém os parâmetros anteriores; cabeçalhos e chave de
idempotência são opcionais, assim como a substituição do remetente por mensagem.
O remetente padrão do serviço compartilhado não é modificado por envios de marketing.
Não altera cadastro, sessão ou conversão.

`MARKETING_FROM_EMAIL` é opcional e versões anteriores a ignoram. Para desfazer
somente a separação do remetente, remova a variável e reimplante o backend; os
envios de marketing voltam a usar `CONTACT_FROM_EMAIL`.

A versão anterior ignora `MARKETING_TEST_RECIPIENT`; esta versão exige a variável
para disparos de teste. Os links de descadastro existentes continuam válidos,
inclusive quando a variável estiver ausente ou mudar. Em rollback, desabilitar
o gatilho de envio antes de restaurar uma versão com destinatário fixo.

Rollback: remover o botão/gatilho de envio primeiro. Preservar as rotas de
descadastro enquanto houver e-mails enviados com esses links. Reverter toda a
entrega deixaria esses links indisponíveis; não restaurar consentimentos revogados.
Monitorar erros `marketing_test_delivery_failed` e conferir o ID aceito no Resend.
Para reverter apenas o e-mail 01, restaure o frontend anterior e depois o backend
anterior. Não apague nem restaure consentimentos; preserve os links existentes.

## Validação

Executar testes da API de marketing, sessão admin, verificação de e-mail e
contato; executar também `test_marketing_email_http.py`, que usa HTTP real para
o app e um simulador local do Resend. Este último verifica idempotência,
descadastro, bloqueio posterior e conversão CSV/relatório sem enviar e-mail real.
`POST /analyze` está desativado na aplicação atual (405); o caminho feliz usa
`POST /convert`. Executar a suíte completa, Ruff e os lints de texto/navegação.
O script `scripts/smoke_admin_marketing.py` verifica desktop/celular, botão
desabilitado durante envio, simulação, erro e repetição com a mesma chave.
