# Templates de e-mail — OFX Simples / conteúdo e cursos

Templates editoriais em HTML, independentes do backend. Abra `index.html` no
navegador para comparar as quatro mensagens em desktop e celular. A prévia usa
dados fictícios e uma cópia dos templates, e não envia e-mails. Ao editar um
template, atualize também sua cópia em `index.html` e a alternativa em texto.

| Arquivo | Ordem do envio manual | Objetivo |
| --- | --- | --- |
| `01-organizacao.html` | 1º | Qualidade do arquivo, dificuldades de leitura e precisão na conversão |
| `03-curso.html` | 2º | Indicação do curso da Descomplicont |
| `02-revisao.html` | 3º | Guia prático de revisão dentro do OFX Simples |
| `04-excel-powerbi.html` | Alternativa | Oferta alternativa de Excel e Power BI |

O quarto arquivo é uma opção independente de oferta, sem data de disparo. Não
é um quarto passo automático da sequência. Escolha o curso adequado ao público:
Descomplicont para quem prepara demonstrações contábeis, Excel e Power BI para
quem quer trabalhar com planilhas e análise de dados. Ao usar a segunda oferta,
adapte também a mensagem educativa anterior para esse tema.

Cada HTML possui uma alternativa `.txt`. `campaign.json` reúne os assuntos,
preheaders e a ordem definida para envio manual. `delivery_mode` é `manual` e
os dias sugeridos ficaram vazios: não há intervalo definido nem agendamento
implementado. Os nomes e IDs antigos foram mantidos; a ordem válida é a da
tabela e do array `emails`, não a numeração dos nomes de arquivo.

## Primeiro e-mail: qualidade do arquivo (rascunho para revisão)

Assunto: **Antes de converter: 4 detalhes para conferir no seu extrato**.
O nome `01-organizacao` foi preservado para manter as referências existentes.
A abertura agora usa quatro situações práticas: download original versus
impressão em PDF, digitalização inclinada, informação cortada e descrição
distribuída em duas linhas, mesmo em um layout conhecido.

Os pequenos extratos são fictícios e desenhados com tabelas HTML e texto, sem
imagens externas. Cada exemplo explica a dificuldade e orienta uma ação. A
inclinação é esquemática, representada pelo deslocamento das células; o corte
é indicado por reticências e uma borda tracejada. As legendas mantêm o sentido
dos exemplos mesmo quando o cliente de e-mail altera a apresentação.

O texto prioriza fidelidade e revisão quando há incerteza, sem prometer precisão
absoluta, rejeitar todo PDF impresso ou tratar todo download como garantia de
boa leitura. A alternativa `.txt` preserva o conteúdo e descreve os exemplos.

Aprendizados usados como referência editorial interna:

- `docs/pdf-split-sheet-recovery-20261021.md`: colunas separadas por impressão,
  conteúdo sobreposto e preservação de informações sem inventar dígitos.
- `docs/pdf-recovery-afternoon-20261021.md`: variações dentro dos layouts,
  interpretação de sinais no contexto e controles financeiros.
- `AGENTS.md`, visão de extração financeira: preservar o sentido dos movimentos
  e explicitar ambiguidades sem completar dados ilegíveis.

Este primeiro rascunho segue disponível na prévia para iterar texto e exemplos;
nenhuma campanha foi ativada e nenhum e-mail foi enviado.

## E-mail de revisão no OFX Simples

Assunto: **Como revisar seu extrato no OFX Simples**.
Abra `index.html#02-revisao` para ir diretamente a essa mensagem. O guia segue
a interface compartilhada das páginas gratuitas, com quatro passos: resumo,
avisos, edição e download. Ensina a usar os campos do editor, salvar, cancelar,
voltar ao original pelo menu da linha e baixar novamente após editar.

O PDF original é aberto pelo cliente em outra janela. Não são anunciados
visualizador lado a lado, filtros, aprovação por linha ou outros recursos do
workspace PRO. Avisos de validação incompleta não são descritos como prova de
erro; referência compartilhada não é tratada como duplicidade; saldos não
devem ser ajustados apenas para eliminar diferenças.

Fontes de conferência internas:

- `frontend/ofx-convert.js` e `frontend/ofx-convert.css`: resumo, avisos,
  editor, ações da linha e exportação nas páginas do conversor.
- `docs/pdf-review-guidance.md`: significado e limites dos avisos.
- `scripts/smoke_free_editor.py` e `scripts/smoke_free_review_frontend.py`:
  navegação e edição exercitadas em navegador, desktop e celular.

### Imagem da interface e preparação do envio

`assets/free-review-editor.png` é uma captura do editor real renderizado
localmente, em formato de celular, com lançamento fictício de R$ 320,00 em
Débito e Crédito vazio. Não contém informações de clientes. Foi capturada em
390 × 600 CSS px, com escala 2; o HTML a exibe com até 390 px de largura.
Texto, legenda e alternativa `.txt` explicam o passo mesmo sem a imagem.

Para reproduzir a captura, a partir da raiz do repositório:

```powershell
backend/venv/Scripts/python.exe scripts/capture_marketing_free_review.py
```

O script usa Chromium/Playwright já instalado no ambiente de desenvolvimento,
carrega o frontend local e simula as respostas da API com dados fictícios. As
requisições externas são bloqueadas. Ele não altera a aplicação nem envia
dados para o serviço em produção.

O caminho relativo da imagem no HTML funciona na prévia local. **Antes do
envio manual, insira o PNG no corpo pelo editor do provedor de e-mail** ou
substitua o caminho por uma URL HTTPS pública que você tenha publicado. Não
envie o HTML mantendo o caminho local `assets/...`.

Para um futuro envio MIME com imagem incorporada, `inline_images` no item de
revisão de `campaign.json` registra o arquivo, o tipo e o Content-ID. O remetente
deve substituir `assets/free-review-editor.png` por
`cid:ofx-free-review-editor` e anexar o PNG como `image/png`, com
`Content-ID: <ofx-free-review-editor>` e `Content-Disposition: inline`, numa
parte `multipart/related`. Esse preparo ainda não está integrado ao backend.
Se o provedor não aceitar imagens, use a alternativa `.txt` ou remova somente
a imagem; as instruções continuam completas. Envie uma prova ao próprio
remetente antes do disparo para conferir a renderização e os links.

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
- Fontes do sistema, sem fontes remotas, JavaScript ou formulários nos e-mails.
  O e-mail de revisão inclui um PNG com texto alternativo e instruções completas
  em texto. Os demais e-mails não usam imagens. O JavaScript existe somente na
  página local de prévia.
- Preheader oculto, títulos semânticos, links visíveis e alternativas em texto.
- Fallback de largura para Outlook clássico via comentário condicional MSO.
- Verificação visual em navegador não substitui testes em Gmail, Outlook e
  Apple Mail. Ainda não houve envio real para esses clientes.

Os arquivos não estão conectados às rotas da aplicação e não modificam o envio
fixo de teste. Não há mudança de banco, API, worker ou agendamento. A futura
integração deve registrar campanha/destinatário para evitar duplicidades e
reutilizar o descadastro existente. Para rollback deste pacote, basta remover
os arquivos; os links de descadastro do serviço continuam intactos.
