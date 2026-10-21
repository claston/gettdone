# OFX Simples PRO

Aplicação Next.js independente para o workspace profissional de revisão em lote. O fluxo Free existente não é alterado.

## Execução local

```powershell
cd pro
npm install
npm run dev
```

Abra `http://localhost:3000/demo/review/batch-setembro-2026` para acessar o vertical slice completo com dados de demonstração, sem depender do backend.

## Scripts

- `npm run dev`: servidor de desenvolvimento.
- `npm run build`: build de produção.
- `npm run test:run`: suíte unitária e de integração.
- `npm run lint`: ESLint.
- `npm run typecheck`: TypeScript sem emissão.
- `npm run format:check`: validação de formatação.

## Rotas

- `/dashboard`
- `/conversions`
- `/clients`
- `/periods`
- `/reconciliation`
- `/account`
- `/demo/review/[batchId]`
- `/demo/review/[batchId]/[conversionId]`
- `/review/[batchId]`
- `/review/[batchId]/[conversionId]`

## Arquitetura de dados

A UI consome somente a interface `WorkspaceRepository`, definida em `src/domain/workspace.ts`. As rotas `/demo/review/*` sempre usam `src/data/mock-workspace-repository.ts`, mesmo quando a API está configurada. Assim, a demonstração continua disponível no mesmo build caso o backend esteja indisponível.

As rotas `/review/*` usam o repository configurado. Quando `NEXT_PUBLIC_API_URL` está presente, `src/data/http-workspace-repository.ts` adapta os contratos reais de lote, edição, revisão, retry, PDF privado e exportação ZIP sem expor o formato bruto do backend aos componentes. Sem a variável, essas rotas mantêm o fallback mock por compatibilidade com o desenvolvimento local.

O upload real em `/conversions` é autenticado e exclusivamente assíncrono:

1. consulta `/auth/me` para reutilizar uma sessão autenticada em cookie `HttpOnly`;
2. quando não existe sessão, solicita login e usa `/auth/pro/session/login`, protegido por origem e pelo header próprio do PRO;
3. consulta `/api/conversion-runtime` e exige `direct_s3` + `sqs_lambda`;
4. cria o lote em `/api/pro/conversion-batches`;
5. envia cada arquivo diretamente para a URL temporária do armazenamento privado;
6. submete o lote para a fila do worker e abre `/review/{batchId}`.

O PRO nunca chama `/api/conversions/upload`. Se o runtime assíncrono estiver indisponível, a interface interrompe o fluxo antes de calcular hashes, criar o lote ou enviar arquivos e mostra uma mensagem de indisponibilidade. Não existe fallback inline.

O preview usa cookies host-only próprios. Uma sessão criada em `www.ofxsimples.com.br` não autentica automaticamente `ofxsimples-pro-preview.onrender.com`; para o canário, entre novamente no preview com o usuário pago cujo e-mail está em `CONVERSION_ASYNC_USER_EMAIL_ALLOWLIST`. O proxy expõe somente as rotas de autenticação necessárias ao PRO, sem encaminhar todo o namespace `/auth/*`.

As fixtures de demonstração cobrem conversões prontas, com suspeitas, revisadas, processando e com falha; o extrato demonstrativo é servido localmente em `/mock-statement`. A interface identifica esse modo com o selo `Dados de demonstração`.

### Revisão de lançamentos

Campos monetários são comparados pelo valor numérico: entrar e sair da célula ou mudar apenas sua formatação não cria uma alteração. O saldo é calculado pelo backend e aparece somente para consulta. As gravações automáticas são serializadas por documento; uma edição feita durante uma requisição permanece pendente até sua própria gravação. Em caso de erro, o workspace mantém as alterações e oferece uma nova tentativa.

O botão **Adicionar** abre um diálogo com data, descrição e crédito ou débito. Ao confirmar, o lançamento aparece no topo da lista contínua e a rolagem volta ao início. Após a gravação, a lista fica ordenada por data. Cancelar o diálogo não altera o documento.

O seletor **Organizar lançamentos** alterna entre lista contínua e separação por página de origem. O cabeçalho de cada página permite abrir a página correspondente no PDF. A opção acompanha a navegação entre documentos durante a revisão. Lançamentos manuais e resultados antigos sem procedência ficam no grupo **Sem página de origem**. A informação `source_page` é aditiva no JSON de transações e não exige migração SQL; resultados anteriores precisam ser reprocessados para obter esse dado.

O PDF privado é carregado pelo mesmo cliente autenticado das APIs, com renovação de sessão e botão para recarregar em caso de falha. Somente o documento aberto fica em um blob temporário no navegador, liberado ao trocar de arquivo. Cookies e verificação de proprietário continuam obrigatórios.

### Acompanhamento entre PDF e lançamentos

O visualizador controlado usa React-PDF **9.2.1** e PDF.js **4.8.69**, com importação exclusiva do cliente (`next/dynamic`, `ssr: false`). Essa combinação aceita React 19 e Node >=18, preservando o mínimo atual do PRO (20.9); CI e Render continuam em Node **24.21.0**. A versão 10.2 foi avaliada, mas seu PDF.js exige Node >=20.16 ou >=22.3. O Node 21 local gera avisos das ferramentas existentes; para reproduzir o deploy, use Node 24.21.0.

O `predev`/`prebuild` copia o worker **legacy**, cMaps e fontes de `pdfjs-dist` para `public/pdfjs/<versão>/`, ignorado pelo Git e ESLint. Não há CDN nem envio do PDF a outro serviço. O worker legacy e o complemento local de `Promise.withResolvers` preservam a base de navegadores do Next (Chrome/Edge 111+, Firefox 111+, Safari 16.4+). O smoke em Chromium também remove essa API antes de iniciar o app para verificar o complemento; versões antigas de cada navegador ainda exigem homologação própria. Referências: [React-PDF 9](https://github.com/wojtekmaj/react-pdf/blob/v9.2.1/packages/react-pdf/README.md) e [PDF.js](https://mozilla.github.io/pdf.js/getting_started/).

O acompanhamento é automático e não exige configuração. Ao manter o mouse brevemente sobre uma linha, ela ganha destaque azul e o PDF acompanha sua origem, destacando as regiões reais da extração. Ao rolar a tabela, a linha próxima ao início da área de leitura conduz o PDF. No sentido inverso, rolar o PDF escolhe o lançamento visível cuja região fica mais próxima da posição de leitura dentro da página; a tabela acompanha e ambos recebem o destaque. A seleção usa IDs estáveis, sem comparar campos editáveis. Funciona na lista contínua e na separação por páginas, preservando filtros e ordem.

O PDF mostra um marcador de leitura automático: uma faixa azul atravessa a página na altura da região selecionada, com um indicador na margem esquerda. A faixa acompanha a linha azul do revisor por hover e rolagem nos dois sentidos, sem ativar **Marcar no PDF**. Fragmentos sobrepostos da mesma linha compartilham a faixa; linhas separadas e continuações mantêm suas próprias regiões. Também é possível mover o mouse ao lado dos caracteres no PDF para selecionar a linha reconhecida naquela altura. Cabeçalhos, espaços vazios e páginas sem coordenadas não recebem uma associação inventada. Essa navegação não grava marcações nem altera valores ou o estado de revisão. As marcações manuais continuam opcionais e separadas.

Apenas a rolagem iniciada pelo usuário conduz o outro painel. A rolagem automática não devolve comandos ao painel que a iniciou; o mouse parado sobre uma tabela que se move não deve trocar a seleção. O hover tem um pequeno intervalo para evitar saltos ao atravessar linhas rapidamente. Deslocamentos próximos do PDF usam transição suave, respeitando a preferência de movimento reduzido do navegador. O acompanhamento e o hover pausam enquanto uma célula editável mantém foco e retomam na posição atual do PDF ao sair da edição. Não há controle para ligar/desligar nem faixa explicativa durante a navegação normal. O botão de origem continua disponível para teclado e dispositivos sem hover.

Páginas sem lançamentos ou sem resultados no filtro mostram avisos sem limpar filtros nem deslocar a grade para outra página. Lançamentos manuais/antigos sem `sourcePage` continuam editáveis e indicam ausência de origem. Falhas de acesso/renderização oferecem nova tentativa sem descartar a revisão. Visitar páginas não salva nem marca lançamentos como revisados.

As dimensões das páginas são lidas antes da apresentação; apenas páginas na área de leitura e próximas dela mantêm canvases (densidade limitada a 1,5). Espaços com dimensões reais mantêm a rolagem estável, inclusive com páginas de tamanhos diferentes. Zoom e redimensionamento preservam página e posição proporcional dentro dela. A camada de texto e links internos está desativada nesta entrega; PDFs escaneados são exibidos normalmente e o original pode ser aberto para consulta adicional.

### Localização da origem por região

As regiões reconhecidas no próprio PDF também podem ser selecionadas com hover, clique, toque ou foco pelo teclado. O revisor vai até o lançamento correspondente e o marca em azul, sem reposicionar o PDF. Os alvos usam somente regiões reais de lançamentos visíveis no filtro; páginas e linhas sem coordenadas não recebem alvos inventados. A seleção direta permanece até uma nova seleção ou rolagem do usuário. Durante a edição, o hover no PDF fica pausado para preservar o foco.

A ação de origem de cada lançamento usa seu ID estável. Quando existem coordenadas reais da extração, o visualizador abre a primeira região e destaca as regiões associadas, inclusive em outras páginas. Zoom e redimensionamento mantêm o destaque no original. Entrar em um campo não move o PDF; editar data, descrição ou valor não redefine as coordenadas. A seleção é limpa ao trocar de documento, e localizar uma origem não marca o lançamento como revisado. Não há configuração adicional.

O campo opcional e aditivo `source_regions` acompanha cada transação na API e no JSON de análise: uma lista de `{page, left, top, width, height}`. `page` começa em 1; os demais valores são frações entre 0 e 1, com origem no canto superior esquerdo da área visível original, **após recorte e rotação intrínseca do PDF**. O adaptador HTTP traduz para `sourceRegions`, sem enviar proveniência nos patches editáveis. O backend preserva a lista ao salvar, excluir/restaurar e reabrir, inclusive via S3 e snapshot do lote. Não há migração SQL.

No OCR, as regiões vêm das células, linhas e palavras efetivamente consumidas pelo parser. Índice de linha não é confundido com índice de linha de tabela. No texto nativo, PDFium fornece os limites reais dos caracteres: eles são associados ao fluxo original selecionado pelo parser por igualdade integral das linhas, com correspondência única quando a ordem dos extratores difere. Quando o fluxo interno contém colunas separadas, como em alguns Sicoob, fragmentos alinhados na mesma altura são reunidos pelas caixas reais e novamente comparados por igualdade integral. Objetos sobrepostos não são fundidos. Linhas repetidas só são associadas quando a igualdade do fluxo completo permite desambiguar pela posição. Não há busca aproximada pelo texto editado, matching por valor/data, nova chamada de OCR ou estimativa visual baseada no número da linha.

O montador multilinha e o parser Wise registram as linhas que consumiram; o adaptador de tabelas/janelas OCR e o layout mensal Itaú preservam as caixas de suas evidências. Outros parsers podem fornecer somente a linha de ancoragem: seu destaque representa essa evidência original, sem afirmar que delimita toda a transação. A lista suporta regiões em várias páginas quando há referências explícitas; esta entrega não passa a interpretar continuações que os parsers atuais ainda não reconhecem.

Conversões antigas, lançamentos manuais e origens ambíguas continuam utilizáveis. Sem regiões válidas, a localização e o acompanhamento usam somente `sourcePage`; sem página, indicam ausência de origem. Geometria não é inventada quando o texto dos extratores diverge ou quando a contagem de páginas não permite confirmar a página física. Resultados antigos não precisam ser reprocessados para abrir e revisar; somente conversões com evidência disponível ganham os destaques e o acompanhamento dentro da página. O destaque é uma camada visual e não altera o PDF original, os dados financeiros ou o estado de revisão. Reconhecimento de manuscritos e seleção livre de texto permanecem separados.

### Marcações manuais de revisão

Selecione um lançamento na tabela (a linha fica azul), pressione **Marcar no PDF** e arraste um retângulo sobre a área desejada. O retângulo é vinculado ao ID daquele lançamento e salvo pelo autosave da revisão. Você pode adicionar regiões em outras páginas, inclusive em documentos escaneados ou resultados antigos sem coordenadas de extração. Cada gesto marca uma única página; arrastar para fora dela recorta o retângulo aos seus limites. O zoom e a rotação/recorte intrínsecos já são considerados. Redimensionar a página durante um gesto cancela o rascunho para evitar coordenadas incorretas.

Durante o desenho, o lançamento escolhido permanece vinculado: hover e rolagem não trocam a linha azul. A ferramenta interrompe a rolagem suave antes de iniciar o gesto; mover a página durante um gesto descarta o rascunho para evitar uma região deslocada. Ao concluir, o modo de desenho termina. **Esc**, **Cancelar marcação** ou **Ctrl+Z** durante o desenho descartam o rascunho sem salvar. **Limpar marcações** remove as marcações manuais do lançamento; **Desfazer/Ctrl+Z** depois de uma alteração restaura o estado anterior pela mesma história de edição da grade. Trocar de documento, filtro ou seleção encerra o desenho. Uma nova célula em edição também encerra o desenho e mantém a proteção do foco.

Marcações manuais aparecem com contorno tracejado discreto e recebem destaque ao selecionar seu lançamento. Clicar/tocar nelas também seleciona o revisor. Elas têm prioridade sobre alvos automáticos sobrepostos, permitindo associar uma área a um lançamento diferente daquele sugerido pela extração. A localização e o acompanhamento consideram as regiões manuais junto das automáticas, mantendo a evidência original intacta.

O campo aditivo `review_regions` usa a mesma geometria normalizada de `source_regions`, mas armazena apenas marcações do revisor (até 50 por lançamento). O contrato de edição aceita a ação `annotate` para alterar somente essa lista. Essas operações validam página/geometria, proprietário, versão e ID de operação; não recalculam saldos nem regeneram relatórios/OFX. Edições financeiras, exclusão/restauração, reabertura no S3 e snapshot do lote preservam a lista. O PRO exige confirmação das regiões na resposta antes de mostrar sucesso, mantendo alterações locais e oferecendo nova tentativa quando a gravação falha.

As marcações ficam na revisão: não são gravadas nos bytes do PDF original nem incorporadas ao PDF aberto pelo link de consulta. Não alteram valores, datas, descrições, regras de extração/conciliação, nem marcam automaticamente o arquivo como revisado. Resultados antigos carregam `review_regions: []` e podem receber marcações sem reconversão.

Para publicar, atualize **API e worker antes do PRO** para receber o contrato de marcações. Não há migração SQL nem backfill; o armazenamento usa os JSONs existentes e mantém TTL/autorização. O frontend anterior ignora o campo, e a API nova preserva as marcas quando ele envia edições financeiras sem esse campo. Para rollback, reverta somente o PRO e mantenha a API nova, preservando os JSONs e suas marcações. Reverter também a API para um serializador antigo pode descartar marcações ao regravar análises; preserve o JSON antes desse rollback. Sem a API nova, o PRO não confirma a gravação das marcações.

Para validar em navegador real com dados exclusivamente sintéticos:

```powershell
cd pro
$env:NEXT_PUBLIC_API_URL = "/"
npm run build
npm run start -- -H 127.0.0.1 -p 3100
# Em outro terminal, na raiz do repositório:
backend\venv\Scripts\python.exe scripts/smoke_pro_pdf_sync.py
```

O script usa o Playwright Python existente, intercepta autenticação/API, gera um PDF de 30 páginas (incluindo página só com imagem, página rotacionada e continuação em outra página) e 541 lançamentos. As coordenadas nativas vêm do mesmo associador usado pelo backend; a página só com imagem recebe uma caixa OCR sintética explícita. Valida hover sem clique, rolagem real nos dois sentidos dentro da página, ausência de ciclos e estabilidade com mouse parado. Além dos filtros e da proteção do foco contra hover/rolagem, lê os pixels do canvas sob o destaque para conferir a presença do conteúdo original. Inclui seleção diretamente no PDF, desenho com mouse e toque real em 390px, cancelamento sem gravação, limpeza/desfazer, associação sobre outra região automática, regiões em várias páginas e reabertura completa preservando as marcas. Confere os dados financeiros e a extração original após essas ações. Capturas sintéticas ficam em `tmp/pro-pdf-sync/`. Nenhum PDF privado participa dos testes.

As edições do PRO também atualizam o snapshot em `conversion_jobs.result_payload`, preservando os metadados da instituição, para que o lote reaberto apresente as linhas e a versão salvas. O backend deve ser atualizado antes do frontend; o worker deve receber a mesma versão para que novas conversões incluam `source_page`. Conflitos reais com outra sessão não são sobrescritos automaticamente.

### Busca, painéis e conclusão do lote — OFX-33

**Buscar no PDF** consulta a camada textual localmente, ignora caixa/acentos e permite navegar entre até 500 ocorrências (o indicador `500+` informa o limite). A busca destaca as caixas dos fragmentos originais, inclusive em páginas com recorte/rotação, sem alterar a seleção da revisão nem os dados. Enter/Shift+Enter avançam/voltam; Esc limpa a consulta. O índice é construído sob demanda, reutilizado apenas no documento atual e descartado ao trocar de arquivo. PDFs sem texto recebem um aviso; a busca não chama OCR nem envia a consulta ao servidor.

O separador entre PDF e lançamentos permite arrastar, usar setas esquerda/direita (5 pontos percentuais), Home/End (25%/75%) e Enter/clique duplo (restaurar 45%). Os botões recolhem cada painel sem desmontar seu conteúdo. Em telas estreitas, ambos continuam empilhados. Alt+↑/↓ muda a linha mantendo a coluna em edição; Alt+PageUp/PageDown muda o arquivo. Ctrl+Enter confirma revisão somente depois de validar que não há gravação pendente; campos com erro não concluem o documento.

**Exportar lote** aguarda todas as gravações pendentes e em andamento, inclusive em arquivos fora da tela. Uma falha de gravação impede a exportação, mantém a correção e seleciona o documento afetado. Falhas de exportação, revisão e reprocessamento exibem nova tentativa; falhas de atualização não removem edições locais. Durante a exportação, edição/desfazer ficam indisponíveis. A consulta individual também exige que o arquivo esteja salvo. Fechar o aviso de exportação preserva o workspace. Quando todos os arquivos estão revisados, a interface confirma a conclusão do lote.

O visualizador recebe diretamente o `Blob` já autenticado. A URL temporária fica disponível para abrir o original e é revogada na troca de arquivo; o renderizador não inicia outra requisição XHR contra essa URL. Isso evita o erro de cabeçalhos vazios do PDF.js ao cancelar carregamentos em navegação rápida.

### Telemetria e aceitação — OFX-43 / OFX-44

O repository HTTP envia apenas `event_type` e `page_path: "/review"` para `/telemetry/events`: `workspace_opened`, `conversion_selected`, `suspicious_filter_used`, `transaction_edited`, `conversion_reviewed` e `batch_export_requested`. O modo demo não envia eventos. Não são enviados nomes de arquivos, IDs de lote/documento, consultas, descrições, datas ou valores. Edições consecutivas durante a mesma gravação são agrupadas em um evento de uso. O backend exige usuário registrado, origem permitida e cabeçalho PRO; fixa novamente a rota e descarta metadados extras antes de gravar na tabela existente `product_events`. Falhas de telemetria não interrompem a revisão. Não há nova tabela, migração SQL ou mudança de retenção.

Além do smoke de PDF acima, a aceitação completa usa:

```powershell
backend\venv\Scripts\python.exe scripts/smoke_pro_workspace.py --url http://127.0.0.1:3100
```

São 12 documentos sintéticos, um com 541 lançamentos. O teste abre o lote, edita, troca de arquivo, recupera falhas de salvamento/polling, acompanha uma conversão pronta e outra com falha/retry, pesquisa no PDF rotacionado, ajusta painéis, navega por teclado, revisa todos os documentos e confere o ZIP baixado. Verifica a privacidade dos seis eventos, a ausência de erros JavaScript e o canvas preservado durante edição. Abertura até tabela+PDF visíveis tem orçamento de **12 s**, e confirmação visual da edição, **1 s**. São limites do cenário controlado de navegador, com API interceptada; não medem latência S3, fila ou OCR de produção. Métricas, ZIP e capturas exclusivamente sintéticas ficam em `tmp/pro-workspace-acceptance/`. A CI executa os dois smokes no build de produção e preserva os artefatos.

Publicação: atualizar API e worker antes do PRO. A melhoria de coordenadas beneficia conversões novas; resultados antigos sem regiões precisam de nova conversão para ganhar o destaque automático e continuam revisáveis pelo fallback por página ou pelas marcações manuais. Não há backfill nem alteração financeira. O PRO anterior ignora os novos eventos/campos. Rollback: reverter o frontend e/ou o associador de coordenadas; eventos e evidências já armazenados continuam compatíveis com a API existente.

## Configuração

Copie `.env.example` para `.env.local` e ajuste `NEXT_PUBLIC_API_URL` para apontar ao backend. A autenticação usa cookies de sessão e todas as requisições são enviadas com credenciais. Os endpoints mutáveis do PRO exigem o cabeçalho `X-OFX-Pro-Client: web` e uma origem exata permitida pelo backend; isso não substitui a validação de propriedade do lote por identidade.

### Preview no Render

O app deve ser publicado como **Web Service Node**, porque as rotas de revisão são dinâmicas e executam com `next start`. O `render.yaml` da raiz configura o serviço gratuito `ofxsimples-pro-preview` com:

- root directory `pro`;
- build `npm ci && npm run build`;
- start `npm run start -- -H 0.0.0.0 -p $PORT`;
- health check `/dashboard`;
- deploy automático somente depois que os checks da branch passarem.

Para revisar somente as telas, use `/demo/review/batch-setembro-2026`. Essa rota não chama as APIs de lote, autenticação, edição ou relatório e funciona com ou sem variáveis de backend.

Para conectar o backend real no Render, configure as duas variáveis no serviço e faça um novo build:

```text
NEXT_PUBLIC_API_URL=/
BACKEND_API_URL=https://www.ofxsimples.com.br
```

Neste estágio, `BACKEND_API_URL` aponta para o backend FastAPI já existente do OFX Simples Free em `https://www.ofxsimples.com.br`; ele não é outro serviço Render. `NEXT_PUBLIC_API_URL=/` faz o navegador usar o mesmo domínio do PRO. O Next.js encaminha somente as rotas explicitamente listadas em `src/config/backend-proxy.ts`, incluindo o runtime, `/api/pro/*`, o bootstrap de sessão e os relatórios. Assim, evita cookies cross-site e não expõe `BACKEND_API_URL` ao bundle do navegador.

Depois de configurar ou alterar `NEXT_PUBLIC_API_URL`, faça um novo build do serviço. Essa variável é incorporada ao bundle do navegador durante o build; um redeploy sem rebuild não aplica a mudança.

No backend, mantenha `CORS_ALLOW_ORIGINS` sem wildcard e inclua obrigatoriamente a origem exata do preview. A validação dos endpoints mutáveis confere o cabeçalho `Origin` mesmo quando a chamada chega pelo proxy same-origin:

```text
CORS_ALLOW_ORIGINS=https://ofxsimples-pro-preview.onrender.com,https://OUTRA-ORIGEM-JA-EXISTENTE
```

Não inclua barra final na origem.

### Pré-requisitos do worker

O upload real só deve ser liberado depois que o worker de conversão estiver atualizado com o mesmo contrato da API. No backend, o runtime efetivo do usuário precisa responder:

```json
{
  "upload_mode": "direct_s3",
  "execution_mode": "sqs_lambda",
  "direct_batch_enabled": true
}
```

Isso requer, no mínimo, a arquitetura `async_aws`, repositório PostgreSQL, bucket privado de conversão e `CONVERSION_SQS_QUEUE_URL` válida. As variáveis completas estão em `backend/.env.example`. Ativar o runtime antes de atualizar o worker pode enfileirar trabalhos que a versão implantada não consegue processar; portanto, a ordem segura é:

1. publicar e validar manualmente a versão compatível do worker;
2. publicar o backend com os endpoints PRO e a origem do preview;
3. ativar `direct_s3 + sqs_lambda`;
4. executar um lote real e conferir processamento, revisão e exportação.

A automação de CD do worker será tratada separadamente. Enquanto esses pré-requisitos não forem atendidos, o modo demo continua funcional e o upload real permanece bloqueado sem recorrer ao caminho inline.
