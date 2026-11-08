import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import Page from "@/app/(protected)/(app)/account/page";
import { ProSessionGate } from "./pro-session-gate";
import type { ProUser } from "@/data/pro-auth-service";

const user = {
  user_id: "usr-account",
  name: "João Gonçalves",
  email: "joao@example.com",
  plan_code: "professional",
  plan_name: "Profissional",
  quota_mode: "pages",
  quota_limit: 1500,
  quota_remaining: 1270,
  max_upload_size_bytes: 10 * 1024 * 1024,
  max_pages_per_file: 100,
};

async function renderAccount(overrides: Partial<typeof user> = {}) {
  const authService = {
    currentUser: vi.fn().mockResolvedValue({ ...user, ...overrides }),
    login: vi.fn(),
    logout: vi.fn(),
  };
  render(
    <ProSessionGate authService={authService}>
      <Page />
    </ProSessionGate>,
  );
  await screen.findByRole("heading", { name: "Conta e plano" });
  return authService;
}

describe("Conta e plano", () => {
  it("mostra os dados reais da sessão na rota de conta, sem o placeholder de ciclo", async () => {
    const authService = await renderAccount();
    const account = within(screen.getByRole("region", { name: "Sua conta" }));
    expect(account.getByText(user.name)).toBeInTheDocument();
    expect(account.getByText(user.email)).toBeInTheDocument();
    expect(screen.getByText("Profissional")).toBeInTheDocument();
    expect(screen.getByText("1.500 páginas")).toBeInTheDocument();
    expect(screen.getByText("230 páginas")).toBeInTheDocument();
    expect(screen.getByText("1.270 páginas")).toBeInTheDocument();
    expect(screen.getByText("10 MB")).toBeInTheDocument();
    expect(screen.getByText("100 páginas")).toBeInTheDocument();
    expect(
      screen.queryByText(/ciclo|mockado|demonstração/i),
    ).not.toBeInTheDocument();
    expect(authService.currentUser).toHaveBeenCalledOnce();
  });

  it("respeita a unidade de conversões e o saldo esgotado", async () => {
    await renderAccount({
      quota_mode: "conversion",
      quota_limit: 5,
      quota_remaining: 0,
    });
    expect(screen.getAllByText("5 conversões")).toHaveLength(2);
    expect(screen.getByText("0 conversões")).toBeInTheDocument();
    expect(screen.queryByText(/páginas disponíveis/i)).not.toBeInTheDocument();
  });

  it("usa o código do plano quando o nome não foi enviado", async () => {
    await renderAccount({ plan_name: "", plan_code: "business" });
    expect(screen.getByText("business")).toBeInTheDocument();
    expect(screen.queryByText("Plano ativo")).not.toBeInTheDocument();
  });

  it("sinaliza dados ausentes sem inventar plano, limites ou saldo", async () => {
    const minimalUser: ProUser = {
      user_id: "usr-minimal",
      name: "Maria",
      email: "maria@example.com",
    };
    render(
      <ProSessionGate
        authService={{
          currentUser: async () => minimalUser,
          login: vi.fn(),
          logout: vi.fn(),
        }}
      >
        <Page />
      </ProSessionGate>,
    );
    expect(await screen.findByText("Maria")).toBeInTheDocument();
    expect(screen.getByText("Plano não informado")).toBeInTheDocument();
    expect(
      screen.getByText(
        "Os dados de uso do plano não estão disponíveis no momento.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(/0 páginas|0 conversões|Plano ativo/),
    ).not.toBeInTheDocument();
  });

  it.each([
    { quota_mode: "unknown" },
    { quota_remaining: -1 },
    { quota_remaining: 1600 },
  ])(
    "não apresenta uso calculado com dados inconsistentes: %j",
    async (overrides) => {
      await renderAccount(overrides);
      expect(
        screen.getByText(
          "Os dados de uso do plano não estão disponíveis no momento.",
        ),
      ).toBeInTheDocument();
      expect(screen.queryByText("230 páginas")).not.toBeInTheDocument();
    },
  );
});
