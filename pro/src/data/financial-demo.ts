import type { Batch, Conversion, Transaction } from "@/domain/workspace";
import type {
  FinancialScenario,
  FinancialFinding,
} from "@/domain/financial-center";
import type { ReceivedService } from "./received-statements-service";
import { createMockWorkspaceRepository } from "./mock-workspace-repository";
import { financialDemoPdf } from "./financial-demo-pdf";

// Synthetic fixture only. No production fallback, network writes or localStorage.
function transaction(
  id: string,
  day: number,
  description: string,
  cents: number,
  pending = false,
): Transaction {
  return {
    id,
    date: `${String(day).padStart(2, "0")}/09/2026`,
    description,
    credit: cents > 0 ? cents / 100 : null,
    debit: cents < 0 ? -cents / 100 : null,
    balance: null,
    sourcePage: 1,
    sourceLine: day,
    sourceSequence: day,
    fieldStates: {
      date: "ORIGINAL",
      description: pending ? "SUSPECTED" : "ORIGINAL",
      credit: "ORIGINAL",
      debit: "ORIGINAL",
      balance: "ORIGINAL",
    },
  };
}
function makeScenario(
  id: string,
  label: string,
  name: string,
): FinancialScenario {
  const empty = id === "empty" || id === "expired";
  const transactions = empty
    ? []
    : [
        transaction(`${id}-1`, 2, "PIX recebido — Cliente Horizonte", 248000),
        transaction(
          `${id}-2`,
          3,
          "Pagamento fornecedor",
          -65000,
          id === "review",
        ),
        transaction(`${id}-3`, 5, "Tarifa de serviços", -2990),
      ];
  const conversion: Conversion = {
    id: `${id}-doc`,
    batchId: `${id}-batch-a`,
    fileName: `Inter_setembro_${id}.pdf`,
    bankName: "Inter",
    period: "Setembro de 2026",
    status: id === "review" ? "NEEDS_REVIEW" : "REVIEWED",
    reviewStatus: "REVIEWED",
    transactionCount: transactions.length,
    suspectedCount: id === "review" ? 1 : 0,
    editedCount: 0,
    pdf:
      id === "expired"
        ? null
        : { url: "/mock-statement?bank=Inter", pageCount: 1 },
    transactions,
  };
  const batch: Batch = {
    id: `financial-${id}`,
    name: "Documentos de demonstração",
    clientName: name,
    period: "Setembro de 2026",
    createdAt: "2026-10-01T12:00:00Z",
    quota: { used: 1, limit: 1, unit: "FILES" },
    conversions: [conversion],
  };
  const findings: FinancialFinding[] = [];
  if (id === "review")
    findings.push({
      id: "review",
      kind: "review",
      title: "Descrição precisa de conferência",
      detail: "Confira o lançamento com o documento de origem no revisor.",
      movementIds: [`${id}-2`],
      blocking: true,
    });
  if (id === "divergence")
    findings.push({
      id: "divergence",
      kind: "divergence",
      title: "Conta de origem em conflito",
      detail:
        "A associação do lançamento exige conferência. Uma ressalva não resolve esse conflito.",
      movementIds: [`${id}-1`],
      blocking: true,
    });
  if (id === "duplicates") {
    transactions.push(
      transaction(`${id}-4`, 3, "Pagamento fornecedor", -65000),
    );
    findings.push({
      id: "duplicate",
      kind: "duplicate",
      title: "Possível duplicidade",
      detail:
        "Duas ocorrências semelhantes. Nenhuma foi removida ou conciliada automaticamente.",
      movementIds: [`${id}-2`, `${id}-4`],
      blocking: true,
    });
  }
  const data: FinancialScenario = {
    id,
    label,
    client: { name, taxId: "CNPJ 00.000.000/0000-00 · fictício" },
    month: "2026-09",
    trial: id === "trial",
    accounts: [{ id: "inter", label: "Inter · Conta ••1234" }],
    sources: [
      {
        conversionId: conversion.id,
        batchId: conversion.batchId,
        accountId: "inter",
        filename: conversion.fileName,
        availability: id === "expired" ? "expired" : "available",
        coverage: {
          from: "2026-09-01",
          to: ["partial", "trial"].includes(id) ? "2026-09-15" : "2026-09-30",
        },
      },
    ],
    findings,
    batch,
  };
  if (id === "multiple") {
    transactions.push(
      transaction(`${id}-4`, 8, "Transferência para conta própria", -40000),
    );
    transactions.push(transaction(`${id}-5`, 12, "Compra no cartão", -12000));
    transactions.push(transaction(`${id}-6`, 13, "Estorno da compra", 12000));
    const second: Conversion = {
      ...conversion,
      id: "multiple-doc-b",
      batchId: "multiple-batch-b",
      fileName: "Itau_setembro.pdf",
      bankName: "Itaú",
      pdf: { url: "/mock-statement?bank=Itaú", pageCount: 1 },
      transactions: [
        transaction(
          "multiple-7",
          8,
          "Transferência recebida de conta própria",
          40000,
        ),
        transaction("multiple-8", 3, "Pagamento fornecedor", -65000),
      ],
    };
    batch.conversions.push(second);
    data.accounts = [
      ...data.accounts,
      { id: "itau", label: "Itaú · Conta ••5678" },
    ];
    data.sources = [
      ...data.sources,
      {
        conversionId: second.id,
        batchId: second.batchId,
        accountId: "itau",
        filename: second.fileName,
        availability: "available",
        coverage: { from: "2026-09-01", to: "2026-09-30" },
      },
    ];
    findings.push(
      {
        id: "transfer",
        kind: "suggestion",
        title: "Transferência entre contas sugerida",
        detail:
          "Valores opostos em duas contas do cliente. Confira as duas origens antes de confirmar qualquer relação.",
        movementIds: ["multiple-4", "multiple-7"],
        blocking: false,
      },
      {
        id: "reversal",
        kind: "suggestion",
        title: "Possível estorno",
        detail:
          "Compra e devolução preservadas como dois movimentos. Sugestão ainda sem confirmação.",
        movementIds: ["multiple-5", "multiple-6"],
        blocking: false,
      },
      {
        id: "unmatched",
        kind: "unmatched",
        title: "Pagamento sem correspondência",
        detail: "Não há evidência comparável disponível para este pagamento.",
        movementIds: ["multiple-2"],
        blocking: false,
      },
    );
  }
  for (const item of batch.conversions) {
    item.transactionCount = item.transactions.length;
    if (item.pdf) item.pdf.url = `financial-demo:${item.id}`;
  }
  return data;
}
export const financialScenarios: readonly FinancialScenario[] = [
  makeScenario("single", "Uma conta e um extrato", "Ateliê Aurora"),
  makeScenario("multiple", "Múltiplas contas e extratos", "Mercado Horizonte"),
  makeScenario("partial", "Cobertura parcial e ressalvas", "Padaria São José"),
  makeScenario("review", "Lançamentos para revisão", "Oficina Boa Vista"),
  makeScenario("expired", "Documento expirado", "Estúdio Ipê"),
  makeScenario("duplicates", "Possíveis duplicidades", "Comércio Oliveira"),
  makeScenario(
    "divergence",
    "Divergência de integridade",
    "Serviços Primavera",
  ),
  makeScenario("empty", "Extrato sem movimentações", "Consultoria Horizonte"),
  makeScenario("trial", "Primeira conversão · degustação 24h", "Café da Praça"),
];
export const getFinancialScenario = (id: string) =>
  financialScenarios.find((item) => item.id === id);
const repositories = new Map<
  string,
  ReturnType<typeof createMockWorkspaceRepository>
>();
export function financialDemoRepository(scenario: FinancialScenario) {
  let repository = repositories.get(scenario.id);
  if (!repository) {
    repository = createMockWorkspaceRepository({
      latencyMs: 30,
      fixture: scenario.batch,
      invalidateReviewOnSave: true,
    });
    repository.loadDocument = async (id) => {
      const conversion = scenario.batch.conversions.find(
        (item) => item.id === id,
      );
      if (
        !conversion ||
        !scenario.sources.some(
          (source) =>
            source.conversionId === id && source.availability === "available",
        )
      )
        throw new Error("Documento indisponível.");
      return financialDemoPdf(scenario, conversion);
    };
    repositories.set(scenario.id, repository);
  }
  return repository;
}
export const financialDemoClients: ReceivedService = {
  async list(_cursor, _signal, _pendingCursor, query = "") {
    const clients = financialScenarios
      .filter((item) =>
        item.client.name
          .toLocaleLowerCase("pt-BR")
          .includes(query.toLocaleLowerCase("pt-BR")),
      )
      .map((item) => ({
        id: item.id,
        name: item.client.name,
        tax_id: "00000000000000",
        trade_name: "",
        updated_at: "2026-10-01T00:00:00Z",
        historical_count: item.sources.length,
        available_count: item.sources.filter(
          (source) => source.availability === "available",
        ).length,
        expired_count: item.sources.filter(
          (source) => source.availability === "expired",
        ).length,
        attention_count: item.findings.length,
        accounts: item.accounts.map((account) => ({
          id: account.id,
          bank_code: account.id === "inter" ? "077" : "341",
          account_number: account.id === "inter" ? "••1234" : "••5678",
          statement_count: item.sources.filter(
            (source) => source.accountId === account.id,
          ).length,
        })),
      }));
    return {
      clients,
      pending: [],
      next_cursor: null,
      totals: {
        statements: financialScenarios.reduce(
          (sum, item) => sum + item.sources.length,
          0,
        ),
        ready: financialScenarios
          .filter((item) => !item.findings.length)
          .reduce((count, item) => count + item.sources.length, 0),
        attention: financialScenarios
          .filter((item) => item.findings.length)
          .reduce((count, item) => count + item.sources.length, 0),
      },
    };
  },
  async refresh() {},
  async edit() {
    throw new Error("Identidade fictícia não editável nesta demonstração.");
  },
  async confirm() {
    throw new Error("Identidade fictícia não editável nesta demonstração.");
  },
};
