# Templates de e-mail — OFX Simples / conteúdo e cursos

Templates editoriais em HTML, independentes do backend. Abra `index.html` no
navegador para comparar as quatro mensagens em desktop e celular. A prévia usa
dados fictícios e uma cópia dos templates, e não envia e-mails. Ao editar um
template, atualize também sua cópia em `index.html` e a alternativa em texto.

| Arquivo | Dia sugerido | Objetivo |
| --- | --- | --- |
| `01-organizacao.html` | 1 | Organização de documentos antes da revisão |
| `02-revisao.html` | 5 | Registro de pendências e documentos de apoio |
| `03-curso.html` | 10 | Indicação do curso da Descomplicont |
| `04-excel-powerbi.html` | A definir | Oferta alternativa de Excel e Power BI |

O quarto arquivo é uma opção independente de oferta, sem data de disparo. Não
é um quarto passo automático da sequência. Escolha o curso adequado ao público:
Descomplicont para quem prepara demonstrações contábeis, Excel e Power BI para
quem quer trabalhar com planilhas e análise de dados. Ao usar a segunda oferta,
adapte também a mensagem educativa anterior para esse tema.

Cada HTML possui uma alternativa `.txt`. `campaign.json` reúne os assuntos,
preheaders e a ordem sugerida. Os dias são relativos ao início da sequência;
não representam um agendamento implementado.

## Personalização

Substitua os quatro campos em HTML e texto antes de enviar:

- `{{ name }}`: nome do destinatário; use `cliente` se não estiver disponível.
- `{{ unsubscribe_url }}`: URL HTTPS individual de descadastro, gerada pelo
  serviço existente de marketing. Não substituir por um endereço genérico.
- `{{ sender_company }}`: identificação real da empresa/remetente.
- `{{ sender_address }}`: endereço postal real do remetente.

Os campos são marcadores, sem dependência de um motor de templates. Escape
valores de texto para HTML e atributos (incluindo aspas) na versão HTML. Valide
a origem HTTPS da URL de descadastro antes de inseri-la. Na alternativa `.txt`,
use os valores originais. O envio deve rejeitar campos ausentes ou marcadores
não substituídos. A prévia não fornece dados válidos de envio.

O assunto precisa ser enviado como cabeçalho pelo provedor; o `<title>` do
documento não configura o assunto. Use o HTML como corpo HTML e a alternativa
`.txt` como corpo de texto da mesma mensagem, não como dois envios separados.
Preserve também os cabeçalhos de descadastro do serviço existente.

## Público e link da oferta

Esta sequência é destinada a contas ativas, com e-mail verificado, opt-in de
marketing vigente e consentimento versão 2, que contempla conteúdos e ofertas
de parceiros. Verifique a elegibilidade novamente antes de cada mensagem.

O terceiro e-mail usa o Hotlink fornecido pelo responsável:
`https://go.hotmart.com/D87342786L`. A indicação de afiliado aparece no cabeçalho
editorial e no corpo, com explicação sobre a possibilidade de comissão.

O quarto e-mail usa `https://go.hotmart.com/V107857392F`, com a mesma indicação
de afiliado. Seu destino é a oferta de Excel e Power BI da Expert Cursos.

Referências de conteúdo: <https://descomplicont.com/dcne-2023> e
<https://experttcursos.com/excelpowerbi>. O texto não anuncia
preço, desconto, prazo promocional ou pontuação profissional. As condições da
oferta devem ser conferidas na página do produtor antes de cada campanha.

## Compatibilidade e escopo

- Layout de tabelas com `role="presentation"`, CSS essencial inline e largura
  fluida até 600 px; ajuste adicional para telas pequenas.
- Fontes do sistema, sem imagens, fontes remotas, JavaScript ou formulários nos
  e-mails. O JavaScript existe somente na página local de prévia.
- Preheader oculto, títulos semânticos, links visíveis e alternativas em texto.
- Fallback de largura para Outlook clássico via comentário condicional MSO.
- Verificação visual em navegador não substitui testes em Gmail, Outlook e
  Apple Mail. Ainda não houve envio real para esses clientes.

Os arquivos não estão conectados às rotas da aplicação e não modificam o envio
fixo de teste. Não há mudança de banco, API, worker ou agendamento. A futura
integração deve registrar campanha/destinatário para evitar duplicidades e
reutilizar o descadastro existente. Para rollback deste pacote, basta remover
os arquivos; os links de descadastro do serviço continuam intactos.
