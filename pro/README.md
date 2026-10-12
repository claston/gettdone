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
