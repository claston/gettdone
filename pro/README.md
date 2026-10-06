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

## Arquitetura do mock

A UI consome somente a interface `WorkspaceRepository`, definida em `src/domain/workspace.ts`. O adapter atual (`src/data/mock-workspace-repository.ts`) mantém o estado em memória, simula latência, falha de persistência, retry, conclusão de revisão e exportação. Um futuro adapter HTTP poderá substituir o mock sem expor à UI o formato bruto do backend existente.

As fixtures cobrem conversões prontas, com suspeitas, revisadas, processando e com falha. O extrato demonstrativo é servido localmente em `/mock-statement`; a prévia determinística no workspace evita depender do plugin nativo de PDF durante a revisão visual.

## Configuração

Copie `.env.example` para `.env.local` quando for necessário apontar para outro ambiente. `NEXT_PUBLIC_API_URL` está documentada agora, mas não é consumida pelo vertical slice mockado.
