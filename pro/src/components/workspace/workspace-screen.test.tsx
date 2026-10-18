import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ProSessionGate } from "@/components/pro-session-gate";
import { createMockWorkspaceRepository } from "@/data/mock-workspace-repository";
import type { Batch, WorkspaceRepository } from "@/domain/workspace";
import { WorkspaceScreen } from "./workspace-screen";

afterEach(() => vi.unstubAllGlobals());

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
  it("mantém a posição de origem ao corrigir a data sem mostrar a sequência interna", async () => {
    const batch = structuredClone(completedBatch);
    const original = batch.conversions[0].transactions[0];
    batch.conversions[0].transactions = [
      {
        ...original,
        id: "first",
        description: "PRIMEIRO",
        date: "30/04/2026",
        sourceSequence: 1,
      },
      {
        ...original,
        id: "second",
        description: "SEGUNDO",
        date: "30/04/2026",
        sourceSequence: 2,
      },
      {
        ...original,
        id: "third",
        description: "TERCEIRO",
        date: "29/04/2026",
        sourceSequence: 3,
      },
    ];
    const repository: WorkspaceRepository = {
      getBatch: vi.fn().mockResolvedValue(batch),
      saveTransactions: vi.fn().mockImplementation(async (_id, rows) => rows),
      markReviewed: vi.fn(),
      retryConversion: vi.fn(),
      exportBatch: vi.fn(),
    };
    render(<WorkspaceScreen batchId={batch.id} repository={repository} />);
    const first = (await screen.findAllByDisplayValue("30/04/2026"))[0];
    fireEvent.change(first, { target: { value: "01/04/2026" } });
    fireEvent.blur(first);
    await waitFor(() => expect(screen.getByText("Salvo")).toBeInTheDocument(), {
      timeout: 2000,
    });
    expect(screen.getAllByRole("row")[1]).toContainElement(
      screen.getByDisplayValue("PRIMEIRO"),
    );
    expect(screen.getAllByRole("row")[2]).toContainElement(
      screen.getByDisplayValue("SEGUNDO"),
    );
    expect(
      screen.getAllByRole("columnheader").map((cell) => cell.textContent),
    ).toEqual(["Data", "Descrição", "Crédito", "Débito", "Saldo", "Ações"]);
  });

  it("valida a inclusão e permite cancelar sem alterar o documento", async () => {
    const repository: WorkspaceRepository = {
      getBatch: vi.fn().mockResolvedValue(completedBatch),
      saveTransactions: vi.fn(),
      markReviewed: vi.fn(),
      retryConversion: vi.fn(),
      exportBatch: vi.fn(),
    };
    render(
      <WorkspaceScreen batchId={completedBatch.id} repository={repository} />,
    );
    fireEvent.click(await screen.findByRole("button", { name: "Adicionar" }));
    const dialog = screen.getByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("Descrição"), {
      target: { value: "Não incluir" },
    });
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Incluir lançamento" }),
    );
    expect(within(dialog).getByRole("alert")).toHaveTextContent(
      "Informe um valor positivo",
    );
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancelar" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.queryByDisplayValue("Não incluir")).not.toBeInTheDocument();
    expect(repository.saveTransactions).not.toHaveBeenCalled();
    expect(screen.getAllByRole("row")).toHaveLength(2);
  });
  it("inclui pelo diálogo, mostra no início e ordena pela data depois de salvar", async () => {
    let finish: (() => void) | undefined;
    const repository: WorkspaceRepository = {
      getBatch: vi.fn().mockResolvedValue(completedBatch),
      saveTransactions: vi.fn().mockImplementation(
        () =>
          new Promise<void>((resolve) => {
            finish = resolve;
          }),
      ),
      markReviewed: vi.fn(),
      retryConversion: vi.fn(),
      exportBatch: vi.fn(),
    };
    render(
      <WorkspaceScreen batchId={completedBatch.id} repository={repository} />,
    );
    fireEvent.click(await screen.findByRole("button", { name: "Adicionar" }));
    const dialog = screen.getByRole("dialog", { name: "Adicionar lançamento" });
    fireEvent.change(within(dialog).getByLabelText("Data"), {
      target: { value: "20/10/2026" },
    });
    fireEvent.change(within(dialog).getByLabelText("Descrição"), {
      target: { value: "Transação manual" },
    });
    fireEvent.change(within(dialog).getByLabelText("Crédito"), {
      target: { value: "50,00" },
    });
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Incluir lançamento" }),
    );
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getAllByRole("row")[1]).toContainElement(
      screen.getByDisplayValue("Transação manual"),
    );
    await waitFor(
      () => expect(repository.saveTransactions).toHaveBeenCalled(),
      { timeout: 1800 },
    );
    await act(async () => finish!());
    expect(screen.getAllByRole("row")[2]).toContainElement(
      screen.getByDisplayValue("Transação manual"),
    );
  });

  it("agrupa pela página de origem e permite abrir a página correspondente", async () => {
    const batch = structuredClone(completedBatch);
    batch.conversions[0].pdf!.pageCount = 3;
    batch.conversions[0].transactions[0].sourcePage = 3;
    const repository: WorkspaceRepository = {
      getBatch: vi.fn().mockResolvedValue(batch),
      saveTransactions: vi.fn(),
      markReviewed: vi.fn(),
      retryConversion: vi.fn(),
      exportBatch: vi.fn(),
    };
    render(<WorkspaceScreen batchId={batch.id} repository={repository} />);
    fireEvent.change(await screen.findByLabelText("Organizar lançamentos"), {
      target: { value: "PAGES" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Ver página 3 no PDF" }),
    );
    expect(
      screen.getByText("3", { selector: ".pdf-controls strong" }),
    ).toBeInTheDocument();
    expect(screen.getByDisplayValue("PIX concluído")).toBeInTheDocument();
  });

  it("carrega o PDF autenticado, mostra retry e libera o blob ao sair", async () => {
    const batch = structuredClone(completedBatch);
    batch.conversions[0].pdf!.url =
      "/api/pro/conversion-batches/b/jobs/j/document";
    const create = vi.fn().mockReturnValue("blob:test-pdf");
    const revoke = vi.fn();
    vi.stubGlobal(
      "URL",
      class extends URL {
        static createObjectURL = create;
        static revokeObjectURL = revoke;
      },
    );
    const repository: WorkspaceRepository = {
      getBatch: vi.fn().mockResolvedValue(batch),
      saveTransactions: vi.fn(),
      markReviewed: vi.fn(),
      retryConversion: vi.fn(),
      exportBatch: vi.fn(),
      loadDocument: vi
        .fn()
        .mockRejectedValueOnce(new Error("Falha temporária"))
        .mockResolvedValue(new Blob(["%PDF"], { type: "application/pdf" })),
    };
    const { unmount } = render(
      <WorkspaceScreen batchId={batch.id} repository={repository} />,
    );
    fireEvent.click(
      await screen.findByRole("button", { name: "Recarregar PDF" }),
    );
    await waitFor(() =>
      expect(document.querySelector("iframe")?.src).toContain("blob:test-pdf"),
    );
    expect(repository.loadDocument).toHaveBeenCalledTimes(2);
    unmount();
    expect(revoke).toHaveBeenCalledWith("blob:test-pdf");
    vi.unstubAllGlobals();
  });
  it.each(["ofx", "csv", "xlsx"])(
    "mostra o formato %s sem erro de PDF e mantém os lançamentos",
    async (extension) => {
      const batch: Batch = {
        ...completedBatch,
        conversions: [
          {
            ...completedBatch.conversions[0],
            fileName: `extrato.${extension}`,
            pdf: null,
          },
        ],
      };
      const repository: WorkspaceRepository = {
        getBatch: vi.fn().mockResolvedValue(batch),
        saveTransactions: vi.fn(),
        markReviewed: vi.fn(),
        retryConversion: vi.fn(),
        exportBatch: vi.fn(),
      };
      render(<WorkspaceScreen batchId={batch.id} repository={repository} />);

      expect(
        await screen.findByText(
          `Arquivo original em ${extension.toUpperCase()}`,
        ),
      ).toBeInTheDocument();
      expect(
        screen.getByRole("table", { name: "Lançamentos extraídos" }),
      ).toBeInTheDocument();
      expect(screen.getByDisplayValue("PIX concluído")).toBeInTheDocument();
      expect(
        screen.queryByText("Não foi possível processar o arquivo"),
      ).not.toBeInTheDocument();
      expect(document.querySelector("iframe")).toBeNull();
    },
  );

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
