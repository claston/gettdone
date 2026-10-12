# OFX Simples PRO

Aplicação Next.js independente para o workspace profissional de revisão em lote. O fluxo Free existente não é alterado.

## Execução local

```powershell
cd pro
npm install
npm run dev
```

Abra `http://localhost:3000/review/batch-setembro-2026` para acessar o vertical slice completo.

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
- `/review/[batchId]`
- `/review/[batchId]/[conversionId]`

## Arquitetura de dados

A UI consome somente a interface `WorkspaceRepository`, definida em `src/domain/workspace.ts`. Quando `NEXT_PUBLIC_API_URL` está configurada, `src/data/http-workspace-repository.ts` adapta os contratos reais de lote, edição, revisão, retry, PDF privado e exportação ZIP sem expor o formato bruto do backend aos componentes.

Sem a variável, o app usa `src/data/mock-workspace-repository.ts` para a demonstração isolada. As fixtures cobrem conversões prontas, com suspeitas, revisadas, processando e com falha; o extrato demonstrativo é servido localmente em `/mock-statement`.

## Configuração

Copie `.env.example` para `.env.local` e ajuste `NEXT_PUBLIC_API_URL` para apontar ao backend. A autenticação usa os cookies de sessão existentes e todas as requisições são enviadas com credenciais.

### Preview no Render

O app deve ser publicado como **Web Service Node**, porque as rotas de revisão são dinâmicas e executam com `next start`. O `render.yaml` da raiz configura o serviço gratuito `ofxsimples-pro-preview` com:

- root directory `pro`;
- build `npm ci && npm run build`;
- start `npm run start -- -H 0.0.0.0 -p $PORT`;
- health check `/dashboard`;
- deploy automático somente depois que os checks da branch passarem.

Para uma primeira revisão somente das telas, não configure `NEXT_PUBLIC_API_URL` nem `BACKEND_API_URL`; o repository mock continuará ativo.

Para conectar o backend real no Render, configure as duas variáveis no serviço e faça um novo build:

```text
NEXT_PUBLIC_API_URL=/
BACKEND_API_URL=https://URL-DO-BACKEND.onrender.com
```

`NEXT_PUBLIC_API_URL=/` faz o navegador usar o mesmo domínio do PRO. O Next.js encaminha apenas `/api/conversion-batches/*`, `/auth/*`, `/convert-edits/*` e `/convert-report/*` ao backend, evitando cookies cross-site e sem expor `BACKEND_API_URL` ao bundle do navegador. Sessões usadas pelo PRO devem ser iniciadas por `/auth/*` nesse mesmo domínio para que os cookies pertençam ao preview.

No backend, mantenha `CORS_ALLOW_ORIGINS` sem wildcard e inclua a origem exata do preview caso também queira permitir acesso direto à API:

```text
CORS_ALLOW_ORIGINS=https://ofxsimples-pro-preview.onrender.com,https://OUTRA-ORIGEM-JA-EXISTENTE
```

Não inclua barra final na origem. Com o proxy same-origin, o fluxo normal do workspace não depende de CORS; a entrada explícita preserva compatibilidade para diagnósticos e acessos diretos controlados.
