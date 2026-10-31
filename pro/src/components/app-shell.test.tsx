import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AppShell } from "./app-shell";
import { ProSessionGate } from "./pro-session-gate";

vi.mock("next/navigation", () => ({
  usePathname: () => "/conversions",
}));

describe("AppShell", () => {
  it("mostra a identidade e a cota reais e permite encerrar a sessão", async () => {
    const authService = {
      currentUser: vi.fn().mockResolvedValue({
        user_id: "usr-1",
        name: "Usuário Canário",
        email: "canario@example.com",
        plan_name: "Profissional",
        quota_remaining: 127,
        quota_limit: 150,
        quota_mode: "pages",
      }),
      login: vi.fn(),
      logout: vi.fn().mockResolvedValue(undefined),
    };

    render(
      <ProSessionGate authService={authService}>
        <AppShell>
          <div>Área autenticada</div>
        </AppShell>
      </ProSessionGate>,
    );

    expect(await screen.findByText("Usuário Canário")).toBeInTheDocument();
    expect(screen.getByText("Plano Profissional")).toBeInTheDocument();
    expect(screen.getByText("23 de 150 páginas usadas")).toBeInTheDocument();
    expect(screen.queryByText("Integração real")).not.toBeInTheDocument();
    expect(screen.queryByText("Ana Souza")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Sair" }));

    await waitFor(() => expect(authService.logout).toHaveBeenCalledOnce());
    expect(
      await screen.findByRole("heading", { name: "Entre no OFX Simples PRO" }),
    ).toBeInTheDocument();
  });
});
