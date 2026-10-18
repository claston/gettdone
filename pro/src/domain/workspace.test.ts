import { describe, expect, it } from "vitest";

import {
  filterTransactions,
  orderTransactionsForReview,
  nextPendingConversion,
  sortConversionsForReview,
  validateTransactionField,
} from "./workspace";
import type { Conversion, Transaction } from "./workspace";

const conversion = (id: string, status: Conversion["status"]): Conversion => ({
  id,
  batchId: "batch-1",
  fileName: `${id}.pdf`,
  bankName: "Banco",
  period: "Setembro/2026",
  status,
  reviewStatus: status === "REVIEWED" ? "REVIEWED" : "PENDING",
  transactionCount: 1,
  suspectedCount: status === "NEEDS_REVIEW" ? 1 : 0,
  editedCount: 0,
  pdf: { url: "/mock.pdf", pageCount: 2 },
  transactions: [],
});

const transaction = (
  state: Transaction["fieldStates"]["description"],
): Transaction => ({
  id: "transaction-1",
  date: "2026-09-01",
  description: "PIX recebido",
  credit: 120,
  debit: null,
  balance: 120,
  fieldStates: {
    date: "ORIGINAL",
    description: state,
    credit: "ORIGINAL",
    debit: "ORIGINAL",
    balance: "ORIGINAL",
  },
});

describe("workspace domain", () => {
  it("mantém a ordem do arquivo em datas descendentes e ao editar uma data", () => {
    const rows = [
      {
        ...transaction("ORIGINAL"),
        id: "third",
        date: "29/04/2026",
        sourceSequence: 3,
      },
      {
        ...transaction("ORIGINAL"),
        id: "second",
        date: "30/04/2026",
        sourceSequence: 2,
      },
      {
        ...transaction("ORIGINAL"),
        id: "first",
        date: "30/04/2026",
        sourceSequence: 1,
      },
      {
        ...transaction("EDITED"),
        id: "manual",
        sourceSequence: 1,
        sourceSubsequence: 1,
      },
    ];
    expect(orderTransactionsForReview(rows).map((row) => row.id)).toEqual([
      "first",
      "manual",
      "second",
      "third",
    ]);
    rows[2].date = "01/04/2026";
    expect(orderTransactionsForReview(rows).map((row) => row.id)).toEqual([
      "first",
      "manual",
      "second",
      "third",
    ]);
    expect(rows[0].id).toBe("third");
  });

  it("prioriza conversões que precisam de revisão", () => {
    const sorted = sortConversionsForReview([
      conversion("ready", "READY"),
      conversion("failed", "FAILED"),
      conversion("review", "NEEDS_REVIEW"),
      conversion("processing", "PROCESSING"),
    ]);

    expect(sorted.map((item) => item.id)).toEqual([
      "review",
      "ready",
      "processing",
      "failed",
    ]);
  });

  it("encontra a próxima conversão pendente depois da ativa", () => {
    const conversions = [
      conversion("one", "NEEDS_REVIEW"),
      conversion("two", "REVIEWED"),
      conversion("three", "READY"),
    ];

    expect(nextPendingConversion(conversions, "one")?.id).toBe("three");
  });

  it("prioriza a próxima conversão que exige revisão", () => {
    const conversions = [
      conversion("active", "READY"),
      conversion("ready", "READY"),
      conversion("suspected", "NEEDS_REVIEW"),
    ];

    expect(nextPendingConversion(conversions, "active")?.id).toBe("suspected");
  });

  it("filtra lançamentos suspeitos e alterados", () => {
    const suspected = transaction("SUSPECTED");
    const edited = transaction("EDITED");

    expect(filterTransactions([suspected, edited], "SUSPECTED")).toEqual([
      suspected,
    ]);
    expect(filterTransactions([suspected, edited], "EDITED")).toEqual([edited]);
  });

  it("valida datas e valores antes de aceitar uma edição", () => {
    expect(validateTransactionField("date", "31/02/2026")).toBeTruthy();
    expect(validateTransactionField("date", "28/02/2026")).toBeNull();
    expect(validateTransactionField("credit", "1.250,90")).toBeNull();
    expect(validateTransactionField("debit", "valor inválido")).toBeTruthy();
  });
});
