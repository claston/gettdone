import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ProSessionGate } from "@/components/pro-session-gate";
import { createMockWorkspaceRepository } from "@/data/mock-workspace-repository";
import type { Batch, WorkspaceRepository } from "@/domain/workspace";
import { WorkspaceScreen } from "./workspace-screen";

const processingBatch: Batch = {
  id: "batch-live",
  name: "Lote em processamento",
  clientName: "Workspace OFX Simples PRO",
  period: "em processamento",
  createdAt: "2026-10-14T12:00:00Z",
  quota: { used: 1, limit: 50, unit: "FILES" },
  conversions: [
    {
      id: "job-live",
      batchId: "batch-live",
      fileName: "extrato-outubro.pdf",
      bankName: "Instituição não identificada",
      period: "Período indisponível",
      status: "PROCESSING",
      reviewStatus: "PENDING",
      transactionCount: 0,
      suspectedCount: 0,
      editedCount: 0,
      pdf: {
        url: "/mock-statement?bank=Processando",
        pageCount: 1,
        fileName: "extrato-outubro.pdf",
      },
      transactions: [],
    },
  ],
};

const completedBatch: Batch = {
  ...processingBatch,
  name: "Lote 14/10/2026–14/10/2026",
  period: "14/10/2026–14/10/2026",
  conversions: [
    {
      ...processingBatch.conversions[0],
      bankName: "Banco Teste",
      period: "14/10/2026–14/10/2026",
      status: "READY",
      transactionCount: 1,
      transactions: [
        {
          id: "row_1",
          date: "14/10/2026",
          description: "PIX concluído",
          credit: 100,
          debit: null,
          balance: 100,
          fieldStates: {
            date: "ORIGINAL",
            description: "ORIGINAL",
            credit: "ORIGINAL",
            debit: "ORIGINAL",
            balance: "ORIGINAL",
          },
        },
      ],
    },
  ],
};

describe("WorkspaceScreen", () => {
  it("identifica a demonstração e preserva sua rota ao trocar de conversão", async () => {
    render(
      <WorkspaceScreen
        batchId="batch-setembro-2026"
        dataMode="demo"
        repository={createMockWorkspaceRepository({ latencyMs: 0 })}
      />,
    );

    expect(
      await screen.findByText("Dados de demonstração"),
    ).toBeInTheDocument();
    expect(screen.getByTitle("Usuário de demonstração")).toHaveTextContent(
      "UD",
    );

    fireEvent.click(screen.getByRole("option", { name: /Nubank/ }));

    expect(window.location.pathname).toBe(
      "/demo/review/batch-setembro-2026/conv-nubank",
    );
  });

  it("usa a identidade autenticada no workspace real", async () => {
    const authService = {
      currentUser: vi.fn().mockResolvedValue({
        user_id: "usr-1",
        name: "Usuário Canário",
        email: "canario@example.com",
      }),
      login: vi.fn(),
      logout: vi.fn(),
    };

    render(
      <ProSessionGate authService={authService}>
        <WorkspaceScreen
          batchId="batch-setembro-2026"
          repository={createMockWorkspaceRepository({ latencyMs: 0 })}
        />
      </ProSessionGate>,
    );

    expect(await screen.findByTitle("Usuário Canário")).toHaveTextContent("UC");
    expect(screen.queryByText("Dados de demonstração")).not.toBeInTheDocument();
  });

  it("integra navegador, PDF e lançamentos usando somente o repository mock", async () => {
    render(
      <WorkspaceScreen
        batchId="batch-setembro-2026"
        repository={createMockWorkspaceRepository({ latencyMs: 0 })}
      />,
    );

    expect(
      (await screen.findAllByText("Santander_092026.pdf"))[0],
    ).toBeInTheDocument();
    expect(
      screen.getByLabelText("Visualizador do extrato original"),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("table", { name: "Lançamentos extraídos" }),
    ).toBeInTheDocument();
  });

  it("mantém o PDF visível e atualiza os dados quando o processamento termina", async () => {
    let finishRefresh: ((batch: Batch) => void) | undefined;
    const repository: WorkspaceRepository = {
      getBatch: vi
        .fn()
        .mockResolvedValueOnce(processingBatch)
        .mockImplementationOnce(
          () =>
            new Promise<Batch>((resolve) => {
              finishRefresh = resolve;
            }),
        )
        .mockResolvedValue(completedBatch),
      saveTransactions: vi.fn(),
      markReviewed: vi.fn(),
      retryConversion: vi.fn(),
      exportBatch: vi.fn(),
    };

    render(
      <WorkspaceScreen
        batchId="batch-live"
        repository={repository}
        pollIntervalMs={10}
      />,
    );

    expect(
      await screen.findByLabelText("Visualizador do extrato original"),
    ).toBeInTheDocument();
    expect(screen.getByText("Aguardando lançamentos")).toBeInTheDocument();

    await waitFor(() => expect(repository.getBatch).toHaveBeenCalledTimes(2));
    await act(async () => finishRefresh?.(completedBatch));

    expect(
      await screen.findByRole("table", { name: "Lançamentos extraídos" }),
    ).toBeInTheDocument();
    expect(screen.getByDisplayValue("PIX concluído")).toBeInTheDocument();
  });

  it("edita inline, preserva a alteração e apresenta o autosave", async () => {
    render(
      <WorkspaceScreen
        batchId="batch-setembro-2026"
        repository={createMockWorkspaceRepository({ latencyMs: 0 })}
      />,
    );

    const input = (
      await screen.findAllByDisplayValue("Pagamento de boleto")
    )[0];
    fireEvent.change(input, { target: { value: "Pagamento de fornecedor" } });

    expect(screen.getByDisplayValue("Pagamento de fornecedor")).toHaveClass(
      "is-edited",
    );
    await waitFor(() => expect(screen.getByText("Salvo")).toBeInTheDocument(), {
      timeout: 2500,
    });
  });

  it("filtra rapidamente os campos suspeitos", async () => {
    render(
      <WorkspaceScreen
        batchId="batch-setembro-2026"
        repository={createMockWorkspaceRepository({ latencyMs: 0 })}
      />,
    );

    await screen.findAllByText("Santander_092026.pdf");
    fireEvent.click(screen.getByRole("button", { name: /Suspeitos/ }));

    expect(screen.getAllByRole("row")).toHaveLength(4);
  });

  it("preserva a edição ao alternar entre conversões", async () => {
    render(
      <WorkspaceScreen
        batchId="batch-setembro-2026"
        repository={createMockWorkspaceRepository({ latencyMs: 0 })}
      />,
    );

    const input = (
      await screen.findAllByDisplayValue("Pagamento de boleto")
    )[0];
    fireEvent.change(input, { target: { value: "Fornecedor confirmado" } });
    fireEvent.click(screen.getByRole("option", { name: /Nubank/ }));
    fireEvent.click(screen.getByRole("option", { name: /Santander/ }));

    expect(
      screen.getByDisplayValue("Fornecedor confirmado"),
    ).toBeInTheDocument();
  });
});
