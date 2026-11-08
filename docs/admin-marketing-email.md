# Disparo manual de marketing (OFX-7)

No admin, abra **Marketing → Disparo de teste → Enviar e-mail de teste**.
O backend aguarda o Resend na própria requisição e apresenta a aceitação pelo
provedor ou a falha. Não usa worker, fila, agendamento ou tarefa em background.

O destinatário é definido exclusivamente por `MARKETING_TEST_RECIPIENT` no
ambiente do backend, sem campo no painel para informar outro endereço.
A conta deve existir, estar ativa, ter e-mail verificado
e `product_updates_opt_in` habilitado. O sistema não cria usuários nem concede
consentimento automaticamente. O modelo `marketing-test-v1` é texto simples com
saudação, mensagem de teste e link individual para descadastro.

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
- `MARKETING_PUBLIC_BASE_URL`: origem pública que atende as rotas do backend;
  padrão `https://www.ofxsimples.com.br`. Deve ser acessível pelo destinatário e
  encaminhar `/marketing/unsubscribe` ao backend. Em desenvolvimento pode ser
  `http://127.0.0.1:8000`. Não contém caminho, query ou fragmento.
  Origens públicas exigem HTTPS; HTTP é permitido somente no localhost.

Não colocar chaves em arquivos versionados. Não é necessário alterar o worker.

## API e descadastro

- `POST /admin/marketing/test-email`, com sessão admin e JSON
  `{"request_id":"<UUID>"}`. Não aceita destinatário nem conteúdo arbitrário.
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
O Resend mantém as [chaves de idempotência por 24 horas](https://resend.com/docs/dashboard/emails/idempotency-keys).
Os [cabeçalhos de descadastro](https://resend.com/docs/dashboard/emails/add-unsubscribe-to-transactional-emails)
seguem a API do provedor. Não registrar URLs completas de descadastro em logs.

## Evolução e compatibilidade

O serviço de marketing e o modelo ficam separados do gatilho HTTP para permitir
futuras sequências. Esta entrega não implementa o disparo automático ou em massa.
Campanhas futuras precisam verificar a elegibilidade em cada envio e respeitar
a versão de consentimento correspondente ao conteúdo (ofertas/parceiros exigem
a abrangência da versão 2).

Não há migração ou tabela nova. A única escrita persistente é a revogação do
consentimento, usando campos existentes e SQL compatível com SQLite/PostgreSQL.
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

## Validação

Executar testes da API de marketing, sessão admin, verificação de e-mail e
contato; executar também `test_marketing_email_http.py`, que usa HTTP real para
o app e um simulador local do Resend. Este último verifica idempotência,
descadastro, bloqueio posterior e conversão CSV/relatório sem enviar e-mail real.
`POST /analyze` está desativado na aplicação atual (405); o caminho feliz usa
`POST /convert`. Executar a suíte completa, Ruff e os lints de texto/navegação.
O script `scripts/smoke_admin_marketing.py` verifica desktop/celular, botão
desabilitado durante envio, simulação, erro e repetição com a mesma chave.
