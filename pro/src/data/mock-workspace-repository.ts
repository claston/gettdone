import { workspaceFixture } from "./fixtures";
import type {
  Batch,
  ExportResult,
  Transaction,
  WorkspaceRepository,
} from "@/domain/workspace";

interface MockRepositoryOptions {
  latencyMs?: number;
}

export interface MockWorkspaceRepository extends WorkspaceRepository {
  failNextSave(): void;
}

const clone = <T>(value: T): T => structuredClone(value);

export function createMockWorkspaceRepository(
  options: MockRepositoryOptions = {},
): MockWorkspaceRepository {
  const latencyMs = options.latencyMs ?? 650;
  const batch = clone(workspaceFixture);
  let shouldFailSave = false;

  const wait = () => new Promise((resolve) => setTimeout(resolve, latencyMs));
  const findConversion = (conversionId: string) => {
    const conversion = batch.conversions.find(
      (item) => item.id === conversionId,
    );
    if (!conversion) throw new Error("Conversão não encontrada.");
    return conversion;
  };

  return {
    async getBatch(batchId: string): Promise<Batch> {
      await wait();
      if (batchId !== batch.id) throw new Error("Lote não encontrado.");
      return clone(batch);
    },

    async saveTransactions(conversionId: string, transactions: Transaction[]) {
      await wait();
      if (shouldFailSave) {
        shouldFailSave = false;
        throw new Error("Falha simulada ao salvar. Tente novamente.");
      }
      const conversion = findConversion(conversionId);
      conversion.transactions = clone(transactions);
      conversion.editedCount = transactions.filter((transaction) =>
        Object.values(transaction.fieldStates).includes("EDITED"),
      ).length;
    },

    async markReviewed(conversionId: string) {
      await wait();
      const conversion = findConversion(conversionId);
      conversion.status = "REVIEWED";
      conversion.reviewStatus = "REVIEWED";
      conversion.suspectedCount = 0;
    },

    async retryConversion(conversionId: string) {
      await wait();
      const conversion = findConversion(conversionId);
      conversion.status = "PROCESSING";
      conversion.pdf = {
        url: `/mock-statement?bank=${encodeURIComponent(conversion.bankName)}`,
        pageCount: 3,
        fileName: conversion.fileName,
      };
    },

    async exportBatch(): Promise<ExportResult> {
      await wait();
      return {
        fileName: "extratos-setembro-2026.zip",
        conversionCount: batch.conversions.filter(
          (item) => item.status !== "FAILED" && item.status !== "PROCESSING",
        ).length,
      };
    },

    failNextSave() {
      shouldFailSave = true;
    },
  };
}

export const mockWorkspaceRepository = createMockWorkspaceRepository();
