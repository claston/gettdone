# E-mails de compra — galeria de avaliação

Abra `index.html` no navegador. A galeria funciona offline, apresenta cinco e-mails em desktop/celular e permite alternar HTML/texto. No modelo de pagamento, clique em **Abrir página para copiar o link** para avaliar a página de cópia. Os outros links ficam inertes dentro da galeria. O pedido, cliente e link de pagamento são fictícios; nenhum envio, conta ou pagamento é executado.

| Arquivo | Destinatário e momento |
| --- | --- |
| `01-pedido-recebido.html` | Cliente: recebimento do pedido |
| `02-link-pagamento.html` | Cliente: pagamento disponível |
| `03-plano-liberado.html` | Cliente que já possui conta: acesso atual |
| `04-primeiro-acesso.html` | Cliente com conta criada pelo administrador: definir senha |
| `05-aviso-administrador.html` | Equipe: novo pedido para revisão |

Os HTMLs e suas alternativas `.txt` estão em [`backend/app/email_templates/purchase`](../../../backend/app/email_templates/purchase), fonte única usada pelo backend e pela galeria. `flow.json` contém rótulos e dados fictícios de avaliação; assuntos efetivos estão no renderer de produção. `preview-data.js` é gerado para abrir a galeria diretamente do disco, sem servidor.

Após editar HTML, texto ou `flow.json`, execute na raiz:

```powershell
& backend/venv/Scripts/python.exe scripts/build_purchase_email_preview.py
```

O [fluxo implementado](../../../docs/purchase-flow-plan.md) descreve criação de conta somente na liberação administrativa, primeiro acesso, APIs, compatibilidade, recuperação de falhas e implantação. Os templates estão integrados ao backend; esta galeria continua sendo apenas uma demonstração offline.

## Campos dos templates

| Campo | Origem |
| --- | --- |
| `name`, `customer_email`, `order_reference`, `plan_name`, `price_label` | Dados validados e retrato da compra no pedido |
| `customer_whatsapp`, `customer_document`, `customer_notes`, `checkout_terms`, `requested_at` | Dados e aceite do pedido; aviso completo ao backoffice |
| `payment_url` | Link de pagamento validado e gravado pelo administrador |
| `payment_page_url` | Página de visualização/cópia com acesso restrito ao pedido |
| `whatsapp_display`, `whatsapp_url` | Derivados de `WHATSAPP_SUPPORT_NUMBER`; omitir bloco se ausente/inválida |
| `login_url`, `site_url`, `admin_order_url` | Origens e rotas do próprio produto, configuradas/validadas |
| `setup_url`, `setup_expiry_label` | Convite de primeiro acesso emitido somente na liberação administrativa |

O número `5511911808493` da prévia foi lido de `GET https://www.ofxsimples.com.br/public-config` em 12/11/2026. É uma fotografia pública para avaliação visual. A integração deve sempre derivar o número da variável, sem copiar esse valor para uma constante de produção.

O renderer de produção escapa texto/atributos, valida URLs e rejeita campos ausentes. HTML e texto seguem na mesma mensagem; o assunto é um cabeçalho. São mensagens transacionais, sem adesão automática ao marketing e sem conteúdo promocional.

O e-mail usa link para a página de cópia porque JavaScript não é confiável em leitores de e-mail. Os cinco corpos HTML não têm script, formulário, iframe ou eventos inline. O JavaScript da pasta pertence somente à galeria. Referência: [limitações do HTML em e-mails](https://mailchimp.com/help/limitations-of-html-email/).

As URLs com `DEMO` e o domínio `pagamento.example` são fictícios. A produção usa `pagamento.html` e `primeiro-acesso.html` com tokens próprios. Não enviar os exemplos da galeria a clientes. Não houve teste de entrega/renderização real em Gmail, Outlook ou Apple Mail.
