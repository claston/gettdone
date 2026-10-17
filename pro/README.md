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

As edições do PRO também atualizam o snapshot em `conversion_jobs.result_payload`, preservando os metadados da instituição, para que o lote reaberto apresente as linhas e a versão salvas. O backend deve ser atualizado antes do frontend; o worker deve receber a mesma versão para que novas conversões incluam `source_page`. Conflitos reais com outra sessão não são sobrescritos automaticamente.

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
