import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ProSessionGate } from "@/components/pro-session-gate";
import { createMockWorkspaceRepository } from "@/data/mock-workspace-repository";
import { WorkspaceScreen } from "./workspace-screen";

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
