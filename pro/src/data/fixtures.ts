import type { Batch, Conversion, Transaction } from "@/domain/workspace";

const descriptions = [
  "PIX recebido — Comércio Horizonte",
  "Pagamento de boleto",
  "Transferência entre contas",
  "Tarifa pacote de serviços",
  "Recebimento via cartão",
  "PIX enviado — Fornecedor Delta",
  "Aplicação automática",
  "Resgate de investimento",
  "TED recebida",
  "Débito automático — Energia",
  "Pagamento de tributos",
  "Estorno de compra",
];

function makeTransactions(
  prefix: string,
  count: number,
  suspectedRows: number[] = [],
): Transaction[] {
  let balance = 38420.17;
  return Array.from({ length: count }, (_, index) => {
    const isCredit = index % 3 === 0;
    const amount = 145.9 + index * 137.23;
    balance += isCredit ? amount : -amount;
    const suspected = suspectedRows.includes(index);

    return {
      id: `${prefix}-tx-${index + 1}`,
      date: `${String(index + 2).padStart(2, "0")}/09/2026`,
      description: descriptions[index % descriptions.length],
      credit: isCredit ? Number(amount.toFixed(2)) : null,
      debit: isCredit ? null : Number(amount.toFixed(2)),
      balance: Number(balance.toFixed(2)),
      fieldStates: {
        date: "ORIGINAL",
        description: suspected ? "SUSPECTED" : "ORIGINAL",
        credit: "ORIGINAL",
        debit: suspected ? "SUSPECTED" : "ORIGINAL",
        balance: "ORIGINAL",
      },
    };
  });
}

function conversion(
  id: string,
  fileName: string,
  bankName: string,
  status: Conversion["status"],
  transactionCount: number,
  suspectedRows: number[] = [],
): Conversion {
  const transactions =
    status === "PROCESSING" || status === "FAILED"
      ? []
      : makeTransactions(id, Math.min(transactionCount, 18), suspectedRows);

  return {
    id,
    batchId: "batch-setembro-2026",
    fileName,
    bankName,
    period: "01–30 set. 2026",
    status,
    reviewStatus: status === "REVIEWED" ? "REVIEWED" : "PENDING",
    transactionCount,
    suspectedCount: suspectedRows.length,
    editedCount: 0,
    pdf:
      status === "FAILED"
        ? null
        : {
            url: `/mock-statement?bank=${encodeURIComponent(bankName)}`,
            pageCount: 3,
            fileName,
          },
    transactions,
  };
}

export const workspaceFixture: Batch = {
  id: "batch-setembro-2026",
  name: "Extratos Setembro/2026",
  clientName: "Almeida & Torres Contabilidade",
  period: "Setembro de 2026",
  createdAt: "2026-10-05T13:40:00-03:00",
  quota: { used: 23, limit: 50, unit: "FILES" },
  conversions: [
    conversion(
      "conv-santander",
      "Santander_092026.pdf",
      "Santander",
      "NEEDS_REVIEW",
      183,
      [1, 4, 9],
    ),
    conversion("conv-itau", "Itau_092026.pdf", "Itaú", "READY", 126),
    conversion(
      "conv-nubank",
      "Nubank_092026.pdf",
      "Nubank",
      "NEEDS_REVIEW",
      97,
      [2, 5],
    ),
    conversion(
      "conv-bradesco",
      "Bradesco_092026.pdf",
      "Bradesco",
      "REVIEWED",
      211,
    ),
    conversion(
      "conv-bb",
      "Banco_do_Brasil_092026.pdf",
      "Banco do Brasil",
      "PROCESSING",
      0,
    ),
    conversion("conv-caixa", "Caixa_092026.pdf", "Caixa", "FAILED", 0),
    conversion("conv-inter", "Inter_092026.pdf", "Banco Inter", "READY", 84),
    conversion(
      "conv-sicoob",
      "Sicoob_092026.pdf",
      "Sicoob",
      "NEEDS_REVIEW",
      142,
      [0, 7],
    ),
  ],
};
