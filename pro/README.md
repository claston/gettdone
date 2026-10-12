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

As fixtures de demonstração cobrem conversões prontas, com suspeitas, revisadas, processando e com falha; o extrato demonstrativo é servido localmente em `/mock-statement`. A interface identifica esse modo com o selo `Dados de demonstração`.

## Configuração

Copie `.env.example` para `.env.local` e ajuste `NEXT_PUBLIC_API_URL` para apontar ao backend. A autenticação usa os cookies de sessão existentes e todas as requisições são enviadas com credenciais.

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

Neste estágio, `BACKEND_API_URL` aponta para o backend FastAPI já existente do OFX Simples Free em `https://www.ofxsimples.com.br`; ele não é outro serviço Render. `NEXT_PUBLIC_API_URL=/` faz o navegador usar o mesmo domínio do PRO. O Next.js encaminha apenas `/api/conversion-batches/*`, `/auth/*`, `/convert-edits/*` e `/convert-report/*` ao backend, evitando cookies cross-site e sem expor `BACKEND_API_URL` ao bundle do navegador. Sessões usadas pelo PRO devem ser iniciadas por `/auth/*` nesse mesmo domínio para que os cookies pertençam ao preview.

Depois de configurar ou alterar `NEXT_PUBLIC_API_URL`, faça um novo build do serviço. Essa variável é incorporada ao bundle do navegador durante o build; um redeploy sem rebuild não aplica a mudança.

No backend, mantenha `CORS_ALLOW_ORIGINS` sem wildcard e inclua a origem exata do preview caso também queira permitir acesso direto à API:

```text
CORS_ALLOW_ORIGINS=https://ofxsimples-pro-preview.onrender.com,https://OUTRA-ORIGEM-JA-EXISTENTE
```

Não inclua barra final na origem. Com o proxy same-origin, o fluxo normal do workspace não depende de CORS; a entrada explícita preserva compatibilidade para diagnósticos e acessos diretos controlados.
