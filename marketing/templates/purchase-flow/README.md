# E-mails de compra — modelos para avaliação

Abra `index.html` no navegador. A galeria funciona offline, apresenta cinco e-mails em desktop/celular e permite alternar HTML/texto. No modelo de pagamento, clique em **Abrir página para copiar o link** para avaliar a página de cópia. Os outros links ficam inertes dentro da galeria. O pedido, cliente e link de pagamento são fictícios; nenhum envio, conta ou pagamento é executado.

| Arquivo | Destinatário e momento |
| --- | --- |
| `01-pedido-recebido.html` | Cliente: recebimento do pedido |
| `02-link-pagamento.html` | Cliente: pagamento disponível |
| `03-plano-liberado.html` | Cliente que já possui conta: acesso atual |
| `04-primeiro-acesso.html` | Cliente com conta criada pelo administrador: definir senha |
| `05-aviso-administrador.html` | Equipe: novo pedido para revisão |

Cada HTML tem uma alternativa `.txt`. `flow.json` contém assuntos, preheaders, situação de uso e dados de exemplo. `preview-data.js` é gerado a partir desses arquivos para que a galeria possa abrir diretamente do disco, sem servidor.

Após editar HTML, texto ou `flow.json`, execute na raiz:

```powershell
& backend/venv/Scripts/python.exe scripts/build_purchase_email_preview.py
```

O [plano completo](../../../docs/purchase-flow-plan.md) descreve a criação de conta apenas na liberação administrativa, o primeiro acesso por link, as APIs propostas, compatibilidade, recuperação de falhas e critérios de aceite. Este pacote não está conectado ao backend.

## Campos dos templates

| Campo | Origem futura |
| --- | --- |
| `name`, `customer_email`, `order_reference`, `plan_name`, `price_label` | Dados validados e retrato da compra no pedido |
| `customer_whatsapp`, `customer_notes` | Pedido; somente no aviso interno |
| `payment_url` | Link de pagamento validado e gravado pelo administrador |
| `payment_page_url` | Página de visualização/cópia com acesso restrito ao pedido |
| `whatsapp_display`, `whatsapp_url` | Derivados de `WHATSAPP_SUPPORT_NUMBER`; omitir bloco se ausente/inválida |
| `login_url`, `site_url`, `admin_order_url` | Origens e rotas do próprio produto, configuradas/validadas |
| `setup_url`, `setup_expiry_label` | Convite de primeiro acesso emitido somente na liberação administrativa |

O número `5511911808493` da prévia foi lido de `GET https://www.ofxsimples.com.br/public-config` em 12/11/2026. É uma fotografia pública para avaliação visual. A integração deve sempre derivar o número da variável, sem copiar esse valor para uma constante de produção.

Os marcadores não constituem um renderer de produção. Ao integrar: escape texto/atributos, valide URLs, rejeite campos obrigatórios ausentes e qualquer marcador restante. Envie HTML e texto na mesma mensagem; o assunto é um cabeçalho, não o `<title>`. São mensagens transacionais, sem adesão automática ao marketing e sem conteúdo promocional.

O e-mail usa link para a página de cópia porque JavaScript não é confiável em leitores de e-mail. Os cinco corpos HTML não têm script, formulário, iframe ou eventos inline. O JavaScript da pasta pertence somente à galeria. Referência: [limitações do HTML em e-mails](https://mailchimp.com/help/limitations-of-html-email/).

As URLs com `DEMO` e o domínio `pagamento.example` não são rotas de produção. Não enviar estes exemplos a clientes. A galeria apresenta texto e fluxo propostos; a implantação depende de revisão e da implementação descrita no plano. Não houve teste de entrega/renderização real em Gmail, Outlook ou Apple Mail.
